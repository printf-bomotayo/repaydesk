using Npgsql;

namespace Repaydesk.Gateway.Tests;

/// <summary>The pure parts: the approval check, the ledger entry, keys, backoff, settings.</summary>
public class RulesTests
{
    const long Threshold = Settings.DefaultTwoPersonAboveKobo;
    const long OfficerLimit = 50_000_000;
    const long SupervisorLimit = 500_000_000;

    static Posting Posting(long amount, string[] approvedBy, params Allocation[] allocations) =>
        new(1, 10, "bank_csv:NGT1", "RD-P10", new DateOnly(2026, 10, 6), amount,
            allocations.Length > 0 ? allocations : [new Allocation("LN-1", amount)], approvedBy, 0, "bank_csv", "NGT1");

    static ProposalState Approved(Posting p) => new("approved", "match", p.Allocations);

    static LineState Line(Posting p) => new("approved", p.AmountKobo);

    static Decision Approve(string who, string role, long limit) => new(who, role, "approve", limit);

    [Fact]
    public void One_officer_settles_a_payment_up_to_the_threshold()
    {
        var p = Posting(Threshold, ["amaka"]);
        Assert.Null(Mandate.Refusal(p, Approved(p), Line(p), [Approve("amaka", "officer", OfficerLimit)], Threshold));
    }

    [Fact]
    public void A_kobo_over_the_threshold_needs_two_people_one_with_the_limit()
    {
        var p = Posting(Threshold + 1, ["amaka"]);
        Assert.Equal("needs 2 approvals, has 1",
            Mandate.Refusal(p, Approved(p), Line(p), [Approve("amaka", "officer", OfficerLimit)], Threshold));

        var two = Posting(Threshold + 1, ["amaka", "kunle"]);
        Assert.Equal("no approver's limit covers the amount", Mandate.Refusal(two, Approved(two), Line(two),
            [Approve("amaka", "officer", OfficerLimit), Approve("kunle", "officer", OfficerLimit)], Threshold));

        var settled = Posting(Threshold + 1, ["amaka", "tunde"]);
        Assert.Null(Mandate.Refusal(settled, Approved(settled), Line(settled),
            [Approve("amaka", "officer", OfficerLimit), Approve("tunde", "supervisor", SupervisorLimit)], Threshold));
    }

    [Fact]
    public void The_row_cannot_vouch_for_itself()
    {
        var p = Posting(1_000_00, ["tunde"]);
        // Named on the row, but nobody decided: a forged or stray outbox row.
        Assert.Equal("the people named on the posting aren't the people who approved",
            Mandate.Refusal(p, Approved(p), Line(p), [], Threshold));
        Assert.Equal("needs 1 approval, has 0",
            Mandate.Refusal(p with { ApprovedBy = [] }, Approved(p), Line(p), [], Threshold));
        Assert.Equal("the people named on the posting aren't the people who approved",
            Mandate.Refusal(p, Approved(p), Line(p), [Approve("amaka", "officer", OfficerLimit)], Threshold));
        Assert.Equal("an approval came from someone whose role can't approve",
            Mandate.Refusal(p, Approved(p), Line(p), [Approve("tunde", "auditor", 0)], Threshold));
        Assert.Equal("someone rejected this proposal", Mandate.Refusal(p, Approved(p), Line(p),
            [Approve("tunde", "supervisor", SupervisorLimit), new Decision("amaka", "officer", "reject", OfficerLimit)], Threshold));
    }

    [Fact]
    public void The_posting_must_match_what_was_approved()
    {
        var p = Posting(1_000_00, ["amaka"]);
        var ok = new[] { Approve("amaka", "officer", OfficerLimit) };
        Assert.Equal("the proposal is pending, not approved",
            Mandate.Refusal(p, Approved(p) with { Status = "pending" }, Line(p), ok, Threshold));
        Assert.Equal("the proposal is an exclude, which moves no money",
            Mandate.Refusal(p, Approved(p) with { Kind = "exclude" }, Line(p), ok, Threshold));
        Assert.Equal("the payment line is posted, not approved",
            Mandate.Refusal(p, Approved(p), Line(p) with { Status = "posted" }, ok, Threshold));
        Assert.Equal("the posting is for 100000 kobo but the line is 99999",
            Mandate.Refusal(p, Approved(p), Line(p) with { AmountKobo = 99_999 }, ok, Threshold));
        Assert.Equal("the posting's loans and amounts differ from what was approved",
            Mandate.Refusal(p, Approved(p) with { Allocations = [new Allocation("LN-2", 1_000_00)] }, Line(p), ok, Threshold));

        var bad = Posting(1_000_00, ["amaka"], new Allocation("LN-1", 60_000), new Allocation("LN-2", 30_000));
        Assert.Equal("the loans' amounts don't add up to the posting", Mandate.Refusal(bad, Approved(bad), Line(bad), ok, Threshold));
        Assert.Equal("the proposal doesn't exist", Mandate.Refusal(p, null, Line(p), ok, Threshold));
    }

    [Fact]
    public void One_loan_posts_under_the_key_and_a_split_under_numbered_keys()
    {
        Assert.Equal("RD-P10", Posting(100, ["amaka"]).ExternalIdFor(0));
        var split = Posting(100, ["amaka"], new Allocation("LN-1", 60), new Allocation("LN-2", 40));
        Assert.Equal(["RD-P10-1", "RD-P10-2"], [split.ExternalIdFor(0), split.ExternalIdFor(1)]);
    }

    [Fact]
    public void The_ledger_entry_follows_core_bankings_split()
    {
        var p = Posting(100_000_00, ["amaka"], new Allocation("LN-1", 60_500_00), new Allocation("LN-2", 39_500_00));
        var entry = Entries.Repayment(p, [
            new CoreRepayment("LN-1", "RD-P10-1", 7, 50_000_00, 10_500_00, 0, 0),
            new CoreRepayment("LN-2", "RD-P10-2", 8, 30_000_00, 5_000_00, 1_000_00, 3_500_00),
        ]);
        Assert.True(entry.Balanced);
        Assert.Equal("RD-P10", entry.Reference);
        Assert.Equal("RPY", entry.Journal);
        Assert.Equal(new DateOnly(2026, 10, 6), entry.On);
        Assert.Equal(
            [("110100", 100_000_00L, 0L), ("131000", 0, 50_000_00), ("410000", 0, 10_500_00), ("131000", 0, 30_000_00),
             ("410000", 0, 5_000_00), ("420000", 0, 1_000_00), ("219500", 0, 3_500_00)],
            entry.Lines.Select(l => (l.AccountCode, l.DebitKobo, l.CreditKobo)));
    }

    [Fact]
    public void A_split_that_disagrees_with_the_approval_stops_before_the_ledger()
    {
        var p = Posting(100_00, ["amaka"]);
        var e = Assert.Throws<PostingException>(() => Entries.Repayment(p, [new CoreRepayment("LN-1", "RD-P10", 7, 90_00, 0, 0, 0)]));
        Assert.True(e.Permanent);
        Assert.Contains("posted in Fineract, not in the ledger", e.Message);
        Assert.Throws<PostingException>(() => Entries.Repayment(p with { Channel = "carrier_pigeon" },
            [new CoreRepayment("LN-1", "RD-P10", 7, 100_00, 0, 0, 0)]));
    }

    [Fact]
    public void Money_crosses_the_edge_exactly()
    {
        Assert.Equal("1821833.07", Kobo.ToNaira(182_183_307));
        Assert.Equal(182_183_307, Kobo.FromNaira(1821833.07m));
        Assert.Throws<ArgumentException>(() => Kobo.FromNaira(0.005m));
    }

    [Fact]
    public void Retries_back_off_to_ten_minutes()
    {
        Assert.Equal([10, 30, 60, 120, 300, 600, 600], new[] { 1, 2, 3, 4, 5, 6, 40 }.Select(n => (int)Outbox.Backoff(n).TotalSeconds));
    }

    [Fact]
    public void The_shared_database_url_becomes_an_npgsql_connection_string()
    {
        var b = new NpgsqlConnectionStringBuilder(Settings.ToConnectionString(
            "postgresql://repaydesk:p%40ss-W0rd@db:5432/repaydesk?options=-csearch_path%3Dt_abc&sslmode=require"));
        Assert.Equal(("db", 5432, "repaydesk", "repaydesk", "p@ss-W0rd", "t_abc", SslMode.Require),
            (b.Host, b.Port, b.Database, b.Username, b.Password, b.SearchPath, b.SslMode));
        Assert.Equal(5432, new NpgsqlConnectionStringBuilder(Settings.ToConnectionString("postgresql://u:p@h/d")).Port);
    }
}
