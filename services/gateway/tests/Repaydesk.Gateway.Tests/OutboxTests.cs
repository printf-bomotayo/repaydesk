using System.Text.Json;
using Microsoft.Extensions.Logging.Abstractions;
using Npgsql;

namespace Repaydesk.Gateway.Tests;

/// <summary>The drain against a real Postgres, with core banking and the ledger faked.</summary>
public class OutboxTests
{
    static readonly Settings S = new("", "", "", "", "", "", "", "", "", Settings.DefaultTwoPersonAboveKobo, TimeSpan.FromSeconds(15));

    static Outbox Gateway(TestDatabase db, FakeCore core, FakeLedger ledger) =>
        new(db.Source, core, ledger, S, NullLogger<Outbox>.Instance);

    static async Task<string[]> Trail(TestDatabase db, string lineId)
    {
        await using var c = await db.Source.OpenConnectionAsync();
        await using var cmd = new NpgsqlCommand(
            "SELECT action FROM audit_log WHERE subject = @s AND actor = 'service:gateway' ORDER BY id", c);
        cmd.Parameters.AddWithValue("s", $"payment_line:{lineId}");
        var actions = new List<string>();
        await using var r = await cmd.ExecuteReaderAsync();
        while (await r.ReadAsync())
            actions.Add(r.GetString(0));
        return [.. actions];
    }

    [Fact]
    public async Task An_approved_match_posts_to_both_books_and_the_line_says_so()
    {
        await using var db = await TestDatabase.CreateAsync();
        var (id, line, key) = await db.ApprovedAsync(60_500_00);
        var core = new FakeCore();
        var ledger = new FakeLedger();
        var ct = TestContext.Current.CancellationToken;

        Assert.Equal(Outcome.Posted, await Gateway(db, core, ledger).PostNextAsync(ct));
        Assert.Equal(Outcome.Nothing, await Gateway(db, core, ledger).PostNextAsync(ct));

        Assert.Equal(key, Assert.Single(core.Posted.Keys));
        var entry = ledger.Entries[key];
        Assert.Equal(("110100", 60_500_00L), (entry.Lines[0].AccountCode, entry.Lines[0].DebitKobo));
        Assert.Equal("posted", await db.ScalarAsync("SELECT status FROM posting_outbox WHERE id = @id", ("id", id)));
        Assert.Equal($"RPY/{key}", await db.ScalarAsync("SELECT ledger_entry FROM posting_outbox WHERE id = @id", ("id", id)));
        Assert.Equal("posted", await db.ScalarAsync("SELECT status FROM payment_lines WHERE id = @id", ("id", line)));
        var repayments = JsonDocument.Parse((string)(await db.ScalarAsync(
            "SELECT core_transactions::text FROM posting_outbox WHERE id = @id", ("id", id)))!).RootElement;
        Assert.Equal(48_400_00, repayments[0].GetProperty("principal_kobo").GetInt64());
        Assert.Equal(["posting.posted"], await Trail(db, line));
    }

    [Fact]
    public async Task A_crash_between_the_books_resumes_without_posting_twice()
    {
        await using var db = await TestDatabase.CreateAsync();
        var (id, line, key) = await db.ApprovedAsync(25_000_00);
        var core = new FakeCore();
        var ledger = new FakeLedger { FailNextPost = new PostingException("the ledger didn't answer: timeout", permanent: false) };
        var ct = TestContext.Current.CancellationToken;

        // Fineract took it; the ledger didn't answer. Retry later, and say why.
        Assert.Equal(Outcome.Retrying, await Gateway(db, core, ledger).PostNextAsync(ct));
        Assert.Equal(1, core.Posts);
        Assert.Equal("pending", await db.ScalarAsync("SELECT status FROM posting_outbox WHERE id = @id", ("id", id)));
        Assert.Equal(1, await db.ScalarAsync("SELECT attempts FROM posting_outbox WHERE id = @id", ("id", id)));
        Assert.Contains("timeout", (string)(await db.ScalarAsync("SELECT last_error FROM posting_outbox WHERE id = @id", ("id", id)))!);
        Assert.Equal("approved", await db.ScalarAsync("SELECT status FROM payment_lines WHERE id = @id", ("id", line)));
        // The row says Fineract has it, so the case file can show where it stopped.
        Assert.Contains(key, (string)(await db.ScalarAsync("SELECT core_transactions::text FROM posting_outbox WHERE id = @id", ("id", id)))!);

        // Not before its time...
        Assert.Equal(Outcome.Nothing, await Gateway(db, core, ledger).PostNextAsync(ct));
        // ...and when it comes, Fineract's repayment is found, not posted again.
        await db.ExecAsync("UPDATE posting_outbox SET next_attempt_at = now() WHERE id = @id", ("id", id));
        Assert.Equal(Outcome.Posted, await Gateway(db, core, ledger).PostNextAsync(ct));
        Assert.Equal((1, 1, 0), (core.Posts, ledger.Posts, core.Duplicates));
        Assert.Equal(2, await db.ScalarAsync("SELECT attempts FROM posting_outbox WHERE id = @id", ("id", id)));
        Assert.Equal(["posting.retry_scheduled", "posting.posted"], await Trail(db, line));
        Assert.NotNull(ledger.Entries[key]);
    }

    [Fact]
    public async Task An_outbox_row_without_the_decisions_behind_it_is_refused()
    {
        await using var db = await TestDatabase.CreateAsync();
        var (id, line, _) = await db.ApprovedAsync(10_000_00);
        // Someone with database access forges the approval: the row names Tunde, but no decision exists.
        await db.ExecAsync("DELETE FROM decisions");
        await db.ExecAsync("UPDATE posting_outbox SET approved_by = '{tunde}' WHERE id = @id", ("id", id));
        var core = new FakeCore();
        var ledger = new FakeLedger();

        Assert.Equal(Outcome.Refused, await Gateway(db, core, ledger).PostNextAsync(TestContext.Current.CancellationToken));
        Assert.Equal((0, 0), (core.Posts, ledger.Posts));
        Assert.Equal("failed", await db.ScalarAsync("SELECT status FROM posting_outbox WHERE id = @id", ("id", id)));
        Assert.Equal("refused: the people named on the posting aren't the people who approved",
            await db.ScalarAsync("SELECT last_error FROM posting_outbox WHERE id = @id", ("id", id)));
        Assert.Equal(["posting.refused"], await Trail(db, line));
    }

    [Fact]
    public async Task A_large_payment_with_one_approval_is_refused()
    {
        await using var db = await TestDatabase.CreateAsync();
        await db.ApprovedAsync(Settings.DefaultTwoPersonAboveKobo + 1, approvers: ["tunde"]);
        var core = new FakeCore();
        Assert.Equal(Outcome.Refused, await Gateway(db, core, new FakeLedger()).PostNextAsync(TestContext.Current.CancellationToken));
        Assert.Equal(0, core.Posts);
    }

    [Fact]
    public async Task Core_banking_refusing_the_repayment_waits_for_a_person()
    {
        await using var db = await TestDatabase.CreateAsync();
        var (id, line, _) = await db.ApprovedAsync(10_000_00);
        var core = new FakeCore { FailNextPost = new PostingException("Fineract 403: Loan is not active.", permanent: true) };
        var ledger = new FakeLedger();
        var ct = TestContext.Current.CancellationToken;

        Assert.Equal(Outcome.Failed, await Gateway(db, core, ledger).PostNextAsync(ct));
        Assert.Equal(Outcome.Nothing, await Gateway(db, core, ledger).PostNextAsync(ct)); // not retried
        Assert.Equal("Fineract 403: Loan is not active.", await db.ScalarAsync("SELECT last_error FROM posting_outbox WHERE id = @id", ("id", id)));
        Assert.Equal("approved", await db.ScalarAsync("SELECT status FROM payment_lines WHERE id = @id", ("id", line)));
        Assert.Equal(["posting.failed"], await Trail(db, line));
    }

    [Fact]
    public async Task A_split_posts_one_repayment_per_loan_and_one_ledger_entry()
    {
        await using var db = await TestDatabase.CreateAsync();
        var (_, _, key) = await db.ApprovedAsync(90_000_00, allocations: [("LN-2026-000001", 60_000_00), ("LN-2026-000002", 30_000_00)]);
        var core = new FakeCore();
        var ledger = new FakeLedger();

        Assert.Equal(Outcome.Posted, await Gateway(db, core, ledger).PostNextAsync(TestContext.Current.CancellationToken));
        Assert.Equal([$"{key}-1", $"{key}-2"], core.Posted.Keys.Order());
        Assert.Equal(["LN-2026-000001", "LN-2026-000002"], core.Posted.Values.OrderBy(r => r.ExternalId).Select(r => r.LoanRef));
        var entry = Assert.Single(ledger.Entries.Values);
        Assert.Equal(5, entry.Lines.Count); // the collection account, then principal and interest for each loan
        Assert.True(entry.Balanced);
    }

    [Fact]
    public async Task Two_gateways_at_once_post_each_payment_once()
    {
        await using var db = await TestDatabase.CreateAsync();
        for (var i = 0; i < 30; i++)
            await db.ApprovedAsync(1_000_00 + i);
        var core = new FakeCore();
        var ledger = new FakeLedger();
        var ct = TestContext.Current.CancellationToken;

        async Task<int> Drain(Outbox g)
        {
            var n = 0;
            while (await g.PostNextAsync(ct) == Outcome.Posted)
                n++;
            return n;
        }
        var counts = await Task.WhenAll(Drain(Gateway(db, core, ledger)), Drain(Gateway(db, core, ledger)));

        Assert.Equal(30, counts.Sum());
        Assert.Equal((30, 30, 0), (core.Posts, ledger.Posts, core.Duplicates));
        Assert.Equal(30L, await db.ScalarAsync("SELECT count(*) FROM posting_outbox WHERE status = 'posted'"));
    }

    [Fact]
    public async Task An_approval_wakes_the_gateway()
    {
        await using var db = await TestDatabase.CreateAsync();
        var ct = TestContext.Current.CancellationToken;
        await using var listener = await db.Source.OpenConnectionAsync(ct);
        string? heard = null;
        listener.Notification += (_, e) => heard = e.Payload;
        await new NpgsqlCommand("LISTEN posting_outbox", listener).ExecuteNonQueryAsync(ct);

        var (id, _, _) = await db.ApprovedAsync(5_000_00);

        Assert.True(await listener.WaitAsync(TimeSpan.FromSeconds(5), ct));
        Assert.Equal(id.ToString(), heard);
    }

    [Fact]
    public async Task The_database_keeps_a_posted_row_honest()
    {
        await using var db = await TestDatabase.CreateAsync();
        var (id, _, _) = await db.ApprovedAsync(5_000_00);
        var e = await Assert.ThrowsAsync<PostgresException>(() =>
            db.ExecAsync("UPDATE posting_outbox SET status = 'posted', posted_at = now() WHERE id = @id", ("id", id)));
        Assert.Equal("posting_outbox_posted_has_both", e.ConstraintName);
    }
}
