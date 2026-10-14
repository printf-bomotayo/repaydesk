using System.Diagnostics;
using System.Net;
using System.Net.Http.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using Npgsql;
using NpgsqlTypes;

namespace Repaydesk.Gateway;

/// <summary>One approved collections message, as the gateway sends it.</summary>
public sealed record OutgoingMessage(
    long Id,
    string LoanRef,
    string Channel,
    string ToPhone,
    string Body,
    string IdempotencyKey,
    string DecidedBy,
    string DeciderRole,
    bool ChecksPass,
    int Attempts);

public interface IMessaging
{
    /// <summary>
    /// Sends and returns the provider's id. The provider dedupes on the idempotency key, so sending
    /// again after a crash returns the first message's id rather than a second message.
    /// Throws <see cref="PostingException"/>: permanent when the provider refuses the message.
    /// </summary>
    Task<string> SendAsync(OutgoingMessage message, CancellationToken ct);
}

/// <summary>The collections policy's contact hours (docs/collections-policy.md): 08:00 to 18:00, Monday to Saturday, Lagos time.</summary>
public static class ContactHours
{
    /// <summary>West Africa Time is UTC+1 all year (no daylight saving), so no time zone database is needed.</summary>
    static readonly TimeSpan Lagos = TimeSpan.FromHours(1);
    static readonly TimeOnly Opens = new(8, 0), Closes = new(18, 0);

    public static bool Open(DateTimeOffset moment)
    {
        var local = moment.ToOffset(Lagos);
        var time = TimeOnly.FromDateTime(local.DateTime);
        return local.DayOfWeek != DayOfWeek.Sunday && time >= Opens && time < Closes;
    }

    /// <summary>The next moment inside contact hours: <paramref name="moment"/> itself if it is inside.</summary>
    public static DateTimeOffset Next(DateTimeOffset moment)
    {
        var local = moment.ToOffset(Lagos);
        for (var days = 0; days < 8; days++)
        {
            var day = DateOnly.FromDateTime(local.DateTime).AddDays(days);
            if (day.DayOfWeek == DayOfWeek.Sunday)
                continue;
            var start = new DateTimeOffset(day.ToDateTime(Opens), Lagos);
            var end = new DateTimeOffset(day.ToDateTime(Closes), Lagos);
            if (local < start)
                return start;
            if (local < end)
                return local;
        }
        throw new InvalidOperationException("contact hours always come round within a week");
    }

    public static DateOnly Today(DateTimeOffset moment) => DateOnly.FromDateTime(moment.ToOffset(Lagos).DateTime);
}

/// <summary>The messaging provider's HTTP API (the simulator's, in the prototype).</summary>
public sealed class HttpMessaging(HttpClient http, Settings settings) : IMessaging
{
    public async Task<string> SendAsync(OutgoingMessage m, CancellationToken ct)
    {
        using var req = new HttpRequestMessage(HttpMethod.Post, $"{settings.MessagingUrl.TrimEnd('/')}/v1/messages")
        {
            Content = JsonContent.Create(new { channel = m.Channel, to = m.ToPhone, body = m.Body, client_ref = m.IdempotencyKey }),
        };
        req.Headers.Authorization = new("Bearer", settings.MessagingToken);
        HttpResponseMessage res;
        try
        {
            res = await http.SendAsync(req, ct);
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException && !ct.IsCancellationRequested)
        {
            throw new PostingException($"the messaging provider didn't answer: {e.Message}", permanent: false, e);
        }
        using (res)
        {
            var text = await res.Content.ReadAsStringAsync(ct);
            if (res.IsSuccessStatusCode)
                return JsonNode.Parse(text)?["id"]?.GetValue<string>()
                       ?? throw new PostingException("the messaging provider accepted the message without an id", permanent: false);
            var status = (int)res.StatusCode;
            var detail = TryMessage(text) ?? res.ReasonPhrase ?? "";
            // Its refusal of this message (a bad number, too long) won't change on retry; anything
            // systemic (our token, rate limits, its outages) might.
            var systemic = status >= 500 || res.StatusCode is HttpStatusCode.Unauthorized or HttpStatusCode.Forbidden
                or HttpStatusCode.RequestTimeout or HttpStatusCode.TooManyRequests;
            throw new PostingException($"messaging provider {status}: {detail}", permanent: !systemic);
        }
    }

    static string? TryMessage(string text)
    {
        try { return JsonNode.Parse(text)?["message"]?.GetValue<string>(); }
        catch (Exception) { return text.Length > 200 ? text[..200] : text; }
    }
}

/// <summary>
/// Sends approved collections messages, one at a time, the way <see cref="Outbox"/> posts
/// repayments: the row stays locked while the provider is called, and what happened is written in
/// the same transaction with its audit row. Before sending it checks again what could have changed
/// since approval: the approver's right, the policy checks, the contact hours, the week's count,
/// and a promise to pay made since.
/// </summary>
public sealed class MessageOutbox(NpgsqlDataSource db, IMessaging messaging, TimeProvider clock, ILogger<MessageOutbox> log)
{
    const string Actor = "service:gateway";
    const int MaxPerWeek = 2;

    public async Task<Outcome> SendNextAsync(CancellationToken ct)
    {
        var now = clock.GetUtcNow();
        await using var conn = await db.OpenConnectionAsync(ct);
        await using var tx = await conn.BeginTransactionAsync(ct);
        var m = await Next(conn, tx, now, ct);
        if (m is null)
            return Outcome.Nothing;

        using var activity = Telemetry.Source.StartActivity("gateway.send_message");
        activity?.SetTag("repaydesk.idempotency_key", m.IdempotencyKey);
        activity?.SetTag("repaydesk.loan_ref", m.LoanRef);
        activity?.SetTag("repaydesk.channel", m.Channel);

        if (!ContactHours.Open(now))
        {
            var at = ContactHours.Next(now).ToUniversalTime(); // Npgsql writes timestamptz from UTC only
            await Execute(conn, tx, "UPDATE collection_messages SET next_attempt_at = @at WHERE id = @id", ct,
                ("id", m.Id), ("at", at));
            await tx.CommitAsync(ct);
            log.LogInformation("Holding {Key} until contact hours, {At:O}", m.IdempotencyKey, at);
            return Outcome.Deferred;
        }
        if (await Refusal(conn, tx, m, now, ct) is { } refusal)
        {
            await Fail(conn, tx, m, "message.refused", $"refused: {refusal}", ct);
            await tx.CommitAsync(ct);
            log.LogWarning("Refused {Key}: {Reason}", m.IdempotencyKey, refusal);
            activity?.SetStatus(ActivityStatusCode.Error, refusal);
            return Outcome.Refused;
        }
        try
        {
            var providerId = await messaging.SendAsync(m, ct);
            await Execute(conn, tx, """
                UPDATE collection_messages SET status = 'sent', sent_at = @now, provider_id = @provider,
                       attempts = attempts + 1, error = NULL, next_attempt_at = NULL
                WHERE id = @id
                """, ct, ("id", m.Id), ("now", now), ("provider", providerId));
            await Audit(conn, tx, "message.sent", m, new JsonObject
            {
                ["message_id"] = m.Id,
                ["idempotency_key"] = m.IdempotencyKey,
                ["channel"] = m.Channel,
                ["provider_id"] = providerId,
                ["attempt"] = m.Attempts + 1,
            }, ct);
            await tx.CommitAsync(ct);
            log.LogInformation("Sent {Key} by {Channel} for {Loan}: {Provider}", m.IdempotencyKey, m.Channel, m.LoanRef, providerId);
            return Outcome.Sent;
        }
        catch (PostingException e) when (e.Permanent)
        {
            await Fail(conn, tx, m, "message.failed", e.Message, ct);
            await tx.CommitAsync(ct);
            log.LogError("Failed {Key}: {Error}", m.IdempotencyKey, e.Message);
            activity?.SetStatus(ActivityStatusCode.Error, e.Message);
            return Outcome.Failed;
        }
        catch (Exception e) when (!ct.IsCancellationRequested)
        {
            await tx.RollbackAsync(CancellationToken.None);
            var message = e is PostingException ? e.Message : $"unexpected: {e.GetType().Name}: {e.Message}";
            var at = await Retry(m, message, now, ct);
            log.LogWarning("Retrying {Key} at {At:O}: {Error}", m.IdempotencyKey, at, message);
            activity?.SetStatus(ActivityStatusCode.Error, message);
            return Outcome.Retrying;
        }
    }

    static async Task<OutgoingMessage?> Next(NpgsqlConnection conn, NpgsqlTransaction tx, DateTimeOffset now, CancellationToken ct)
    {
        await using var cmd = new NpgsqlCommand("""
            SELECT m.id, m.loan_ref, m.channel, m.to_phone, m.body, m.idempotency_key, m.decided_by, s.role,
                   NOT EXISTS (SELECT 1 FROM jsonb_array_elements(m.checks) c WHERE (c->>'ok')::boolean IS NOT TRUE)
                       AND jsonb_array_length(m.checks) > 0,
                   m.attempts
            FROM collection_messages m JOIN staff s ON s.id = m.decided_by
            WHERE m.status = 'approved' AND coalesce(m.next_attempt_at, m.send_after) <= @now
            ORDER BY coalesce(m.next_attempt_at, m.send_after), m.id
            LIMIT 1
            FOR UPDATE OF m SKIP LOCKED
            """, conn, tx);
        cmd.Parameters.AddWithValue("now", now);
        await using var r = await cmd.ExecuteReaderAsync(ct);
        if (!await r.ReadAsync(ct))
            return null;
        return new OutgoingMessage(r.GetInt64(0), r.GetString(1), r.GetString(2), r.GetString(3), r.GetString(4),
            r.GetString(5), r.GetString(6), r.GetString(7), r.GetBoolean(8), r.GetInt32(9));
    }

    /// <summary>Why this message mustn't go now, or null. Each of these could have changed since approval.</summary>
    static async Task<string?> Refusal(NpgsqlConnection conn, NpgsqlTransaction tx, OutgoingMessage m, DateTimeOffset now,
        CancellationToken ct)
    {
        if (m.DeciderRole is not ("manager" or "supervisor"))
            return $"{m.DecidedBy} ({m.DeciderRole}) may not approve messages";
        if (!m.ChecksPass)
            return "the message didn't pass the collections policy's checks at approval";
        await using (var cmd = new NpgsqlCommand("""
                         SELECT count(*) FROM collection_messages
                         WHERE loan_ref = @loan AND status = 'sent' AND sent_at > @now - interval '7 days'
                         """, conn, tx))
        {
            cmd.Parameters.AddWithValue("loan", m.LoanRef);
            cmd.Parameters.AddWithValue("now", now);
            if ((long)(await cmd.ExecuteScalarAsync(ct))! >= MaxPerWeek)
                return $"{m.LoanRef} has had {MaxPerWeek} messages in the last 7 days";
        }
        await using (var cmd = new NpgsqlCommand("""
                         SELECT due_on FROM promises WHERE loan_ref = @loan AND status = 'open' AND due_on >= @today
                         ORDER BY due_on LIMIT 1
                         """, conn, tx))
        {
            cmd.Parameters.AddWithValue("loan", m.LoanRef);
            cmd.Parameters.AddWithValue("today", ContactHours.Today(now));
            if (await cmd.ExecuteScalarAsync(ct) is DateOnly due)
                return $"the borrower has promised to pay by {due:yyyy-MM-dd}";
        }
        return null;
    }

    static async Task Fail(NpgsqlConnection conn, NpgsqlTransaction tx, OutgoingMessage m, string action, string error,
        CancellationToken ct)
    {
        await Execute(conn, tx, """
            UPDATE collection_messages SET status = 'failed', attempts = attempts + 1, error = @error, next_attempt_at = NULL
            WHERE id = @id
            """, ct, ("id", m.Id), ("error", error));
        await Audit(conn, tx, action, m, new JsonObject
        {
            ["message_id"] = m.Id,
            ["idempotency_key"] = m.IdempotencyKey,
            ["error"] = error,
            ["attempt"] = m.Attempts + 1,
        }, ct);
    }

    async Task<DateTimeOffset> Retry(OutgoingMessage m, string error, DateTimeOffset now, CancellationToken ct)
    {
        // The wait, then the next moment inside contact hours: a retry never goes out at night.
        var at = ContactHours.Next(now + Outbox.Backoff(m.Attempts + 1)).ToUniversalTime();
        await using var conn = await db.OpenConnectionAsync(ct);
        await using var tx = await conn.BeginTransactionAsync(ct);
        await Execute(conn, tx, """
            UPDATE collection_messages SET attempts = attempts + 1, error = @error, next_attempt_at = @at
            WHERE id = @id AND status = 'approved'
            """, ct, ("id", m.Id), ("error", error), ("at", at));
        await Audit(conn, tx, "message.retry_scheduled", m, new JsonObject
        {
            ["message_id"] = m.Id,
            ["idempotency_key"] = m.IdempotencyKey,
            ["error"] = error,
            ["attempt"] = m.Attempts + 1,
            ["next_attempt_at"] = at.ToString("O"),
        }, ct);
        await tx.CommitAsync(ct);
        return at;
    }

    static async Task Audit(NpgsqlConnection conn, NpgsqlTransaction tx, string action, OutgoingMessage m, JsonObject detail,
        CancellationToken ct) =>
        await Execute(conn, tx, """
            INSERT INTO audit_log (actor, action, subject, detail, trace_id)
            VALUES (@actor, @action, @subject, @detail::jsonb, @trace)
            """, ct, ("actor", Actor), ("action", action), ("subject", $"loan:{m.LoanRef}"),
            ("detail", detail.ToJsonString()), ("trace", (object?)Activity.Current?.TraceId.ToHexString() ?? DBNull.Value));

    static async Task Execute(NpgsqlConnection conn, NpgsqlTransaction tx, string sql, CancellationToken ct,
        params (string Name, object Value)[] args)
    {
        await using var cmd = new NpgsqlCommand(sql, conn, tx);
        foreach (var (name, value) in args)
            cmd.Parameters.Add(value is DBNull
                ? new NpgsqlParameter(name, NpgsqlDbType.Text) { Value = DBNull.Value }
                : new NpgsqlParameter(name, value));
        await cmd.ExecuteNonQueryAsync(ct);
    }
}
