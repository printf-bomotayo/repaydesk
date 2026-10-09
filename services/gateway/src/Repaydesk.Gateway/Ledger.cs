namespace Repaydesk.Gateway;

/// <summary>
/// The ledger accounts, the same codes as <c>services/core/src/repaydesk/adapters/chart.py</c>,
/// which the seed creates in Odoo. A code that doesn't exist there fails the posting loudly; it
/// can't land on the wrong account.
/// </summary>
public static class Chart
{
    public const string Principal = "131000";
    public const string InterestIncome = "410000";
    public const string ChargesIncome = "420000";
    public const string Overpayments = "219500";
    public const string RepaymentJournal = "RPY";

    static readonly Dictionary<string, string> Collection = new()
    {
        ["bank_csv"] = "110100",
        ["bank_mt940"] = "110200",
        ["virtual_account"] = "110300",
        ["pos"] = "110400",
        ["branch_cash"] = "110500",
    };

    public static string CollectionAccount(string channel) =>
        Collection.TryGetValue(channel, out var code)
            ? code
            : throw new PostingException($"no collection account for channel {channel}", permanent: true);
}

public static class Entries
{
    /// <summary>
    /// The ledger's side of a posted repayment: debit the collection account the money arrived in,
    /// and credit each loan exactly as core banking applied it. Principal, interest, charges and
    /// any overpayment come from Fineract's own split, so the two books can't disagree about it.
    /// </summary>
    public static JournalEntry Repayment(Posting posting, IReadOnlyList<CoreRepayment> applied)
    {
        if (applied.Count != posting.Allocations.Count)
            throw new ArgumentException("one repayment per loan");
        var lines = new List<JournalLine>
        {
            new(Chart.CollectionAccount(posting.Channel), posting.AmountKobo, 0, $"{posting.Channel} {posting.BankReference}"),
        };
        for (var i = 0; i < applied.Count; i++)
        {
            var r = applied[i];
            var wanted = posting.Allocations[i];
            if (r.LoanRef != wanted.LoanRef || r.TotalKobo != wanted.AmountKobo)
                throw new PostingException(
                    $"Fineract applied {r.TotalKobo} kobo to {r.LoanRef} ({r.ExternalId}) where {wanted.AmountKobo} to " +
                    $"{wanted.LoanRef} was approved; posted in Fineract, not in the ledger", permanent: true);
            void Credit(string account, long kobo, string what)
            {
                if (kobo > 0) lines.Add(new JournalLine(account, 0, kobo, $"{r.LoanRef} {what}"));
            }
            Credit(Chart.Principal, r.PrincipalKobo, "principal");
            Credit(Chart.InterestIncome, r.InterestKobo, "interest");
            Credit(Chart.ChargesIncome, r.ChargesKobo, "fees and penalties");
            Credit(Chart.Overpayments, r.OverpaymentKobo, "overpayment");
        }
        var entry = new JournalEntry(posting.ValueDate, posting.IdempotencyKey,
            $"Repayment {posting.IdempotencyKey}, payment line {posting.LineId}", Chart.RepaymentJournal, lines);
        return entry.Balanced ? entry : throw new InvalidOperationException($"unbalanced entry {entry.Reference}");
    }
}
