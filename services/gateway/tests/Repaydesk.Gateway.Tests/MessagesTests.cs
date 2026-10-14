using System.Collections.Concurrent;
using System.Net;
using System.Text;
using System.Text.Json.Nodes;
using Microsoft.Extensions.Logging.Abstractions;
using Npgsql;

namespace Repaydesk.Gateway.Tests;

/// <summary>A clock the test sets.</summary>
public sealed class Clock(DateTimeOffset now) : TimeProvider
{
    public DateTimeOffset Now { get; set; } = now;
    public override DateTimeOffset GetUtcNow() => Now.ToUniversalTime();
}

/// <summary>The messaging provider as a dictionary keyed like the real one: the same key, the same message.</summary>
public sealed class FakeMessaging : IMessaging
{
    public ConcurrentDictionary<string, (string Id, OutgoingMessage Message)> Received { get; } = new();
    public int Calls;
    /// <summary>Thrown by the next send instead of sending, if set.</summary>
    public PostingException? FailNextSend;

    public async Task<string> SendAsync(OutgoingMessage message, CancellationToken ct)
    {
        await Task.Yield();
        Interlocked.Increment(ref Calls);
        if (Interlocked.Exchange(ref FailNextSend, null) is { } e)
            throw e;
        return Received.GetOrAdd(message.IdempotencyKey, _ => ($"SIM-{Received.Count + 1:000000}", message)).Id;
    }
}

/// <summary>Sending approved messages, against a real Postgres, with the provider faked and the clock set.</summary>
public class MessagesTests
{
    static readonly TimeSpan Lagos = TimeSpan.FromHours(1);
    // Monday 12 October 2026, 10:00 in Lagos.
    static readonly DateTimeOffset MondayMorning = new(2026, 10, 12, 10, 0, 0, Lagos);

    static MessageOutbox Sender(TestDatabase db, IMessaging messaging, Clock clock) =>
        new(db.Source, messaging, clock, NullLogger<MessageOutbox>.Instance);

    static async Task<(string Status, string? Provider, int Attempts, DateTime? NextAttempt, string? Error)> Row(TestDatabase db, long id)
    {
        await using var c = await db.Source.OpenConnectionAsync();
        await using var cmd = new NpgsqlCommand(
            "SELECT status, provider_id, attempts, next_attempt_at, error FROM collection_messages WHERE id = @id", c);
        cmd.Parameters.AddWithValue("id", id);
        await using var r = await cmd.ExecuteReaderAsync();
        await r.ReadAsync();
        return (r.GetString(0), r.IsDBNull(1) ? null : r.GetString(1), r.GetInt32(2),
            r.IsDBNull(3) ? null : r.GetDateTime(3), r.IsDBNull(4) ? null : r.GetString(4));
    }

    static async Task<string[]> Trail(TestDatabase db)
    {
        await using var c = await db.Source.OpenConnectionAsync();
        await using var cmd = new NpgsqlCommand("SELECT action FROM audit_log WHERE actor = 'service:gateway' ORDER BY id", c);
        var actions = new List<string>();
        await using var r = await cmd.ExecuteReaderAsync();
        while (await r.ReadAsync())
            actions.Add(r.GetString(0));
        return [.. actions];
    }

    [Fact]
    public async Task An_approved_message_inside_contact_hours_is_sent_once_and_recorded()
    {
        await using var db = await TestDatabase.CreateAsync();
        var (id, key) = await db.ApprovedMessageAsync(MondayMorning);
        var provider = new FakeMessaging();
        var clock = new Clock(MondayMorning);
        var ct = TestContext.Current.CancellationToken;

        Assert.Equal(Outcome.Sent, await Sender(db, provider, clock).SendNextAsync(ct));
        Assert.Equal(Outcome.Nothing, await Sender(db, provider, clock).SendNextAsync(ct));

        var row = await Row(db, id);
        Assert.Equal(("sent", "SIM-000001", 1), (row.Status, row.Provider, row.Attempts));
        var sent = provider.Received[key].Message;
        Assert.Equal(("sms", "07005123456", "LN-2026-000001"), (sent.Channel, sent.ToPhone, sent.LoanRef));
        Assert.Equal(["message.sent"], await Trail(db));
    }

    [Fact]
    public async Task A_message_is_never_sent_outside_contact_hours_it_waits_for_the_morning()
    {
        await using var db = await TestDatabase.CreateAsync();
        // Approved on a Saturday evening at 19:30: due, but the hours are over.
        var saturdayEvening = new DateTimeOffset(2026, 10, 17, 19, 30, 0, Lagos);
        var (id, _) = await db.ApprovedMessageAsync(saturdayEvening);
        var provider = new FakeMessaging();
        var clock = new Clock(saturdayEvening);
        var ct = TestContext.Current.CancellationToken;

        Assert.Equal(Outcome.Deferred, await Sender(db, provider, clock).SendNextAsync(ct));
        Assert.Equal(0, provider.Calls);
        var held = await Row(db, id);
        // Sunday is not a contact day: Monday 08:00 in Lagos is 07:00 UTC.
        Assert.Equal(("approved", new DateTime(2026, 10, 19, 7, 0, 0)), (held.Status, held.NextAttempt));
        Assert.Equal(0, held.Attempts); // waiting for the hours isn't a failed attempt

        clock.Now = new DateTimeOffset(2026, 10, 18, 12, 0, 0, Lagos); // Sunday noon: not yet
        Assert.Equal(Outcome.Nothing, await Sender(db, provider, clock).SendNextAsync(ct));
        clock.Now = new DateTimeOffset(2026, 10, 19, 8, 0, 0, Lagos);
        Assert.Equal(Outcome.Sent, await Sender(db, provider, clock).SendNextAsync(ct));
    }

    [Fact]
    public async Task A_provider_outage_is_retried_and_the_message_still_goes_once()
    {
        await using var db = await TestDatabase.CreateAsync();
        var (id, _) = await db.ApprovedMessageAsync(MondayMorning);
        var provider = new FakeMessaging { FailNextSend = new PostingException("messaging provider 503: down", permanent: false) };
        var clock = new Clock(MondayMorning);
        var ct = TestContext.Current.CancellationToken;

        Assert.Equal(Outcome.Retrying, await Sender(db, provider, clock).SendNextAsync(ct));
        var waiting = await Row(db, id);
        Assert.Equal(("approved", 1, "messaging provider 503: down"), (waiting.Status, waiting.Attempts, waiting.Error));
        Assert.Equal(MondayMorning.AddSeconds(10).UtcDateTime, waiting.NextAttempt);
        Assert.Equal(Outcome.Nothing, await Sender(db, provider, clock).SendNextAsync(ct)); // not before its time

        clock.Now = MondayMorning.AddSeconds(11);
        Assert.Equal(Outcome.Sent, await Sender(db, provider, clock).SendNextAsync(ct));
        Assert.Single(provider.Received);
        Assert.Equal(("sent", 2), ((await Row(db, id)).Status, (await Row(db, id)).Attempts));
        Assert.Equal(["message.retry_scheduled", "message.sent"], await Trail(db));
    }

    [Fact]
    public async Task A_number_the_provider_refuses_fails_for_a_person()
    {
        await using var db = await TestDatabase.CreateAsync();
        var (id, _) = await db.ApprovedMessageAsync(MondayMorning);
        var provider = new FakeMessaging { FailNextSend = new PostingException("messaging provider 422: 07005123456 can't receive messages", permanent: true) };

        Assert.Equal(Outcome.Failed, await Sender(db, provider, new Clock(MondayMorning)).SendNextAsync(TestContext.Current.CancellationToken));
        var row = await Row(db, id);
        Assert.Equal(("failed", "messaging provider 422: 07005123456 can't receive messages"), (row.Status, row.Error));
        Assert.Equal(["message.failed"], await Trail(db));
    }

    [Fact]
    public async Task What_changed_since_approval_is_checked_before_sending()
    {
        await using var db = await TestDatabase.CreateAsync();
        var ct = TestContext.Current.CancellationToken;
        var provider = new FakeMessaging();
        var clock = new Clock(MondayMorning);

        // Approved by someone without the right (an officer): refused.
        var (byOfficer, _) = await db.ApprovedMessageAsync(MondayMorning, loan: "LN-2026-000010", decidedBy: "amaka");
        Assert.Equal(Outcome.Refused, await Sender(db, provider, clock).SendNextAsync(ct));
        Assert.Contains("may not approve messages", (await Row(db, byOfficer)).Error);

        // A message that failed the policy's checks: refused, whoever approved it.
        var (failing, _) = await db.ApprovedMessageAsync(MondayMorning, loan: "LN-2026-000011", checksPass: false);
        Assert.Equal(Outcome.Refused, await Sender(db, provider, clock).SendNextAsync(ct));
        Assert.Contains("didn't pass the collections policy", (await Row(db, failing)).Error);

        // The borrower promised to pay after the message was approved: it isn't sent.
        await db.ExecAsync("""
            INSERT INTO promises (loan_ref, borrower_ref, amount_kobo, due_on, made_on, source, recorded_by)
            VALUES ('LN-2026-000012', 'CUS-000001', 6050000, DATE '2026-10-16', DATE '2026-10-12', 'staff', 'ngozi')
            """);
        var (promised, _) = await db.ApprovedMessageAsync(MondayMorning, loan: "LN-2026-000012");
        Assert.Equal(Outcome.Refused, await Sender(db, provider, clock).SendNextAsync(ct));
        Assert.Contains("promised to pay by 2026-10-16", (await Row(db, promised)).Error);

        // Two sent this week already: a third isn't.
        var (first, _) = await db.ApprovedMessageAsync(MondayMorning, loan: "LN-2026-000013");
        var (second, _) = await db.ApprovedMessageAsync(MondayMorning, loan: "LN-2026-000013");
        var (third, _) = await db.ApprovedMessageAsync(MondayMorning, loan: "LN-2026-000013");
        Assert.Equal(Outcome.Sent, await Sender(db, provider, clock).SendNextAsync(ct));
        Assert.Equal(Outcome.Sent, await Sender(db, provider, clock).SendNextAsync(ct));
        Assert.Equal(Outcome.Refused, await Sender(db, provider, clock).SendNextAsync(ct));
        Assert.Equal(("sent", "sent", "failed"), ((await Row(db, first)).Status, (await Row(db, second)).Status, (await Row(db, third)).Status));
        Assert.Contains("2 messages in the last 7 days", (await Row(db, third)).Error);
        Assert.Equal(2, provider.Calls);
    }

    [Theory]
    [InlineData("2026-10-12T08:00:00+01:00", true)]   // Monday, opening
    [InlineData("2026-10-12T07:59:00+01:00", false)]
    [InlineData("2026-10-17T17:59:00+01:00", true)]   // Saturday, last minute
    [InlineData("2026-10-17T18:00:00+01:00", false)]
    [InlineData("2026-10-18T12:00:00+01:00", false)]  // Sunday
    [InlineData("2026-10-12T07:30:00+00:00", true)]   // 07:30 UTC is 08:30 in Lagos
    public void Contact_hours_are_the_policys_in_Lagos_time(string at, bool open) =>
        Assert.Equal(open, ContactHours.Open(DateTimeOffset.Parse(at)));

    [Fact]
    public void After_hours_the_next_window_is_the_next_contact_morning()
    {
        Assert.Equal(new DateTimeOffset(2026, 10, 13, 8, 0, 0, Lagos), ContactHours.Next(new(2026, 10, 12, 18, 0, 0, Lagos)));
        Assert.Equal(new DateTimeOffset(2026, 10, 19, 8, 0, 0, Lagos), ContactHours.Next(new(2026, 10, 17, 18, 30, 0, Lagos)));
        Assert.Equal(new DateTimeOffset(2026, 10, 12, 9, 15, 0, Lagos), ContactHours.Next(new(2026, 10, 12, 9, 15, 0, Lagos)));
    }

    sealed class Stub(HttpStatusCode status, string body) : HttpMessageHandler
    {
        public HttpRequestMessage? Request;
        public string? Sent;

        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            Request = request;
            Sent = await request.Content!.ReadAsStringAsync(ct);
            return new HttpResponseMessage(status) { Content = new StringContent(body, Encoding.UTF8, "application/json") };
        }
    }

    static readonly Settings S = new("", "", "", "", "", "", "", "", "", Settings.DefaultTwoPersonAboveKobo,
        TimeSpan.FromSeconds(15), "http://messaging.test", "t0ken");

    static readonly OutgoingMessage M = new(7, "LN-2026-001209", "whatsapp", "07005123456", "Good morning", "CM-7", "ngozi",
        "manager", true, 0);

    [Fact]
    public async Task The_provider_client_sends_the_key_and_reads_the_id()
    {
        var stub = new Stub(HttpStatusCode.Created, """{"id": "SIM-000042", "status": "accepted"}""");
        var id = await new HttpMessaging(new HttpClient(stub), S).SendAsync(M, TestContext.Current.CancellationToken);
        Assert.Equal("SIM-000042", id);
        Assert.Equal("http://messaging.test/v1/messages", stub.Request!.RequestUri!.ToString());
        Assert.Equal("Bearer t0ken", stub.Request.Headers.Authorization!.ToString());
        var sent = JsonNode.Parse(stub.Sent!)!;
        Assert.Equal(("whatsapp", "07005123456", "CM-7"), (sent["channel"]!.GetValue<string>(), sent["to"]!.GetValue<string>(),
            sent["client_ref"]!.GetValue<string>()));
    }

    [Theory]
    [InlineData(HttpStatusCode.UnprocessableEntity, true)]  // this number, this message: a person must look
    [InlineData(HttpStatusCode.BadRequest, true)]
    [InlineData(HttpStatusCode.ServiceUnavailable, false)]  // the provider: try again
    [InlineData(HttpStatusCode.TooManyRequests, false)]
    [InlineData(HttpStatusCode.Unauthorized, false)]         // our token: fix the setting, then it goes
    public async Task The_provider_clients_refusals_are_permanent_or_not(HttpStatusCode status, bool permanent)
    {
        var stub = new Stub(status, """{"error": "x", "message": "can't receive messages"}""");
        var e = await Assert.ThrowsAsync<PostingException>(() =>
            new HttpMessaging(new HttpClient(stub), S).SendAsync(M, TestContext.Current.CancellationToken));
        Assert.Equal(permanent, e.Permanent);
        Assert.Contains($"{(int)status}: can't receive messages", e.Message);
    }
}
