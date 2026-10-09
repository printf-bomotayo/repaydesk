using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging;
using Npgsql;
using NpgsqlTypes;

namespace Repaydesk.Gateway;

public enum Outcome { Nothing, Posted, Retrying, Failed, Refused }

/// <summary>
/// Posts one outbox row at a time. The row stays locked (<c>FOR UPDATE SKIP LOCKED</c>) while the
/// gateway talks to Fineract and the ledger, and what happened is written in that same transaction,
/// with its audit row (ADR 0002). If the gateway dies halfway, the lock goes with it and the row is
/// pending again; the retry looks each write up by its key before making it, so nothing posts twice.
/// Two gateways can run at once: each takes a different row.
/// </summary>
public sealed class Outbox(NpgsqlDataSource db, ICoreBanking core, ILedger ledger, Settings settings, ILogger<Outbox> log)
{
    const string Actor = "service:gateway";

    public async Task<Outcome> PostNextAsync(CancellationToken ct)
    {
        await using var conn = await db.OpenConnectionAsync(ct);
        await using var tx = await conn.BeginTransactionAsync(ct);
        var posting = await Next(conn, tx, ct);
        if (posting is null)
            return Outcome.Nothing;

        using var activity = Telemetry.Source.StartActivity("gateway.post");
        activity?.SetTag("repaydesk.idempotency_key", posting.IdempotencyKey);
        activity?.SetTag("repaydesk.line_id", posting.LineId);
        activity?.SetTag("repaydesk.amount_kobo", posting.AmountKobo);
        var applied = new List<CoreRepayment>();
        try
        {
            var (proposal, line, decisions) = await LoadMandate(conn, tx, posting, ct);
            if (Mandate.Refusal(posting, proposal, line, decisions, settings.TwoPersonAboveKobo) is { } refusal)
            {
                await Fail(conn, tx, posting, "posting.refused", $"refused: {refusal}", applied, ct);
                await tx.CommitAsync(ct);
                log.LogWarning("Refused {Key}: {Reason}", posting.IdempotencyKey, refusal);
                activity?.SetStatus(ActivityStatusCode.Error, refusal);
                return Outcome.Refused;
            }

            for (var i = 0; i < posting.Allocations.Count; i++)
            {
                var a = posting.Allocations[i];
                var externalId = posting.ExternalIdFor(i);
                applied.Add(await core.FindRepaymentAsync(a.LoanRef, externalId, ct)
                            ?? await core.PostRepaymentAsync(a.LoanRef, posting.ValueDate, a.AmountKobo, externalId,
                                posting.Channel, $"Repaydesk {posting.IdempotencyKey}, payment line {posting.LineId}", ct));
            }
            var entry = Entries.Repayment(posting, applied);
            var entryName = await ledger.FindEntryAsync(entry.Reference, ct) ?? await ledger.PostEntryAsync(entry, ct);

            await Posted(conn, tx, posting, applied, entryName, ct);
            await tx.CommitAsync(ct);
            log.LogInformation("Posted {Key}: {Amount} to {Loans}, ledger entry {Entry}", posting.IdempotencyKey,
                Kobo.ToNaira(posting.AmountKobo), string.Join(", ", posting.Allocations.Select(a => a.LoanRef)), entryName);
            return Outcome.Posted;
        }
        catch (PostingException e) when (e.Permanent)
        {
            await Fail(conn, tx, posting, "posting.failed", e.Message, applied, ct);
            await tx.CommitAsync(ct);
            log.LogError("Failed {Key}: {Error}", posting.IdempotencyKey, e.Message);
            activity?.SetStatus(ActivityStatusCode.Error, e.Message);
            return Outcome.Failed;
        }
        catch (Exception e) when (!ct.IsCancellationRequested)
        {
            // Transient (a timeout, a 5xx) or a surprise. Either way: try again later, and say why.
            await tx.RollbackAsync(CancellationToken.None);
            var message = e is PostingException ? e.Message : $"unexpected: {e.GetType().Name}: {e.Message}";
            var at = await Retry(posting, message, applied, ct);
            log.LogWarning("Retrying {Key} at {At:O}: {Error}", posting.IdempotencyKey, at, message);
            activity?.SetStatus(ActivityStatusCode.Error, message);
            return Outcome.Retrying;
        }
    }

    /// <summary>Waits after the nth failed attempt: 10 s, 30 s, 1, 2, 5, then every 10 minutes.</summary>
    public static TimeSpan Backoff(int attempts) => attempts switch
    {
        <= 1 => TimeSpan.FromSeconds(10),
        2 => TimeSpan.FromSeconds(30),
        3 => TimeSpan.FromMinutes(1),
        4 => TimeSpan.FromMinutes(2),
        5 => TimeSpan.FromMinutes(5),
        _ => TimeSpan.FromMinutes(10),
    };

    static async Task<Posting?> Next(NpgsqlConnection conn, NpgsqlTransaction tx, CancellationToken ct)
    {
        await using var cmd = new NpgsqlCommand("""
            SELECT o.id, o.proposal_id, o.line_id, o.idempotency_key, o.value_date, o.amount_kobo,
                   o.allocations::text, o.approved_by, o.attempts, l.channel, l.bank_reference
            FROM posting_outbox o JOIN payment_lines l ON l.id = o.line_id
            WHERE o.status = 'pending' AND coalesce(o.next_attempt_at, o.created_at) <= now()
            ORDER BY coalesce(o.next_attempt_at, o.created_at), o.id
            LIMIT 1
            FOR UPDATE OF o SKIP LOCKED
            """, conn, tx);
        await using var r = await cmd.ExecuteReaderAsync(ct);
        if (!await r.ReadAsync(ct))
            return null;
        return new Posting(r.GetInt64(0), r.GetInt64(1), r.GetString(2), r.GetString(3),
            r.GetFieldValue<DateOnly>(4), r.GetInt64(5), Allocations(r.GetString(6)),
            r.GetFieldValue<string[]>(7), r.GetInt32(8), r.GetString(9), r.GetString(10));
    }

    static IReadOnlyList<Allocation> Allocations(string json) =>
        JsonNode.Parse(json)!.AsArray()
            .Select(a => new Allocation(a!["loan_ref"]!.GetValue<string>(), a["amount_kobo"]!.GetValue<long>()))
            .ToList();

    static async Task<(ProposalState?, LineState?, IReadOnlyList<Decision>)> LoadMandate(
        NpgsqlConnection conn, NpgsqlTransaction tx, Posting p, CancellationToken ct)
    {
        ProposalState? proposal = null;
        await using (var cmd = new NpgsqlCommand(
                         "SELECT status, kind, allocations::text FROM proposals WHERE id = @id FOR UPDATE", conn, tx))
        {
            cmd.Parameters.AddWithValue("id", p.ProposalId);
            await using var r = await cmd.ExecuteReaderAsync(ct);
            if (await r.ReadAsync(ct))
                proposal = new ProposalState(r.GetString(0), r.GetString(1), Allocations(r.GetString(2)));
        }
        LineState? line = null;
        await using (var cmd = new NpgsqlCommand(
                         "SELECT status, amount_kobo FROM payment_lines WHERE id = @id FOR UPDATE", conn, tx))
        {
            cmd.Parameters.AddWithValue("id", p.LineId);
            await using var r = await cmd.ExecuteReaderAsync(ct);
            if (await r.ReadAsync(ct))
                line = new LineState(r.GetString(0), r.GetInt64(1));
        }
        var decisions = new List<Decision>();
        await using (var cmd = new NpgsqlCommand("""
                         SELECT d.staff_id, s.role, d.decision, d.limit_kobo
                         FROM decisions d JOIN staff s ON s.id = d.staff_id
                         WHERE d.proposal_id = @id ORDER BY d.id
                         """, conn, tx))
        {
            cmd.Parameters.AddWithValue("id", p.ProposalId);
            await using var r = await cmd.ExecuteReaderAsync(ct);
            while (await r.ReadAsync(ct))
                decisions.Add(new Decision(r.GetString(0), r.GetString(1), r.GetString(2), r.GetInt64(3)));
        }
        return (proposal, line, decisions);
    }

    static string Repayments(IEnumerable<CoreRepayment> applied) => JsonSerializer.Serialize(applied.Select(r => new
    {
        loan_ref = r.LoanRef,
        external_id = r.ExternalId,
        transaction_id = r.TransactionId,
        principal_kobo = r.PrincipalKobo,
        interest_kobo = r.InterestKobo,
        charges_kobo = r.ChargesKobo,
        overpayment_kobo = r.OverpaymentKobo,
    }));

    static async Task Posted(NpgsqlConnection conn, NpgsqlTransaction tx, Posting p, List<CoreRepayment> applied,
        string entryName, CancellationToken ct)
    {
        var repayments = Repayments(applied);
        await Execute(conn, tx, """
            UPDATE posting_outbox SET status = 'posted', posted_at = now(), attempts = attempts + 1,
                   last_error = NULL, next_attempt_at = NULL, core_transactions = @repayments::jsonb, ledger_entry = @entry
            WHERE id = @id
            """, ct, ("id", p.Id), ("repayments", repayments), ("entry", entryName));
        await Execute(conn, tx, "UPDATE payment_lines SET status = 'posted' WHERE id = @line", ct, ("line", p.LineId));
        await Audit(conn, tx, "posting.posted", p, new JsonObject
        {
            ["idempotency_key"] = p.IdempotencyKey,
            ["outbox_id"] = p.Id,
            ["amount_kobo"] = p.AmountKobo,
            ["core_transactions"] = JsonNode.Parse(repayments),
            ["ledger_entry"] = entryName,
            ["attempt"] = p.Attempts + 1,
        }, ct);
    }

    /// <summary>
    /// A posting that needs a person. What was already posted in Fineract is kept on the row, so
    /// whoever picks it up can see where it stopped.
    /// </summary>
    static async Task Fail(NpgsqlConnection conn, NpgsqlTransaction tx, Posting p, string action, string error,
        List<CoreRepayment> applied, CancellationToken ct)
    {
        var repayments = applied.Count > 0 ? Repayments(applied) : null;
        await Execute(conn, tx, """
            UPDATE posting_outbox SET status = 'failed', attempts = attempts + 1, last_error = @error,
                   next_attempt_at = NULL, core_transactions = coalesce(@repayments::jsonb, core_transactions)
            WHERE id = @id
            """, ct, ("id", p.Id), ("error", error), ("repayments", (object?)repayments ?? DBNull.Value));
        await Audit(conn, tx, action, p, new JsonObject
        {
            ["idempotency_key"] = p.IdempotencyKey,
            ["outbox_id"] = p.Id,
            ["error"] = error,
            ["core_transactions"] = repayments is null ? null : JsonNode.Parse(repayments),
            ["attempt"] = p.Attempts + 1,
        }, ct);
    }

    /// <summary>
    /// Try again later. What Fineract already took is kept on the row, so the case file can say the
    /// repayment is in core banking and waiting for the ledger; the retry finds it by its key.
    /// </summary>
    async Task<DateTimeOffset> Retry(Posting p, string error, List<CoreRepayment> applied, CancellationToken ct)
    {
        var wait = Backoff(p.Attempts + 1);
        var repayments = applied.Count > 0 ? Repayments(applied) : null;
        await using var conn = await db.OpenConnectionAsync(ct);
        await using var tx = await conn.BeginTransactionAsync(ct);
        await using var cmd = new NpgsqlCommand("""
            UPDATE posting_outbox SET attempts = attempts + 1, last_error = @error, next_attempt_at = now() + @wait,
                   core_transactions = coalesce(@repayments::jsonb, core_transactions)
            WHERE id = @id AND status = 'pending'
            RETURNING next_attempt_at
            """, conn, tx);
        cmd.Parameters.AddWithValue("id", p.Id);
        cmd.Parameters.AddWithValue("error", error);
        cmd.Parameters.AddWithValue("wait", wait);
        cmd.Parameters.Add(new NpgsqlParameter("repayments", NpgsqlTypes.NpgsqlDbType.Text) { Value = (object?)repayments ?? DBNull.Value });
        var at = (DateTime?)await cmd.ExecuteScalarAsync(ct);
        await Audit(conn, tx, "posting.retry_scheduled", p, new JsonObject
        {
            ["idempotency_key"] = p.IdempotencyKey,
            ["outbox_id"] = p.Id,
            ["error"] = error,
            ["attempt"] = p.Attempts + 1,
            ["next_attempt_in_seconds"] = (int)wait.TotalSeconds,
            ["core_transactions"] = repayments is null ? null : JsonNode.Parse(repayments),
        }, ct);
        await tx.CommitAsync(ct);
        return at is { } t ? new DateTimeOffset(t, TimeSpan.Zero) : DateTimeOffset.UtcNow + wait;
    }

    static async Task Audit(NpgsqlConnection conn, NpgsqlTransaction tx, string action, Posting p, JsonObject detail,
        CancellationToken ct) =>
        await Execute(conn, tx, """
            INSERT INTO audit_log (actor, action, subject, detail, trace_id)
            VALUES (@actor, @action, @subject, @detail::jsonb, @trace)
            """, ct, ("actor", Actor), ("action", action), ("subject", $"payment_line:{p.LineId}"),
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
