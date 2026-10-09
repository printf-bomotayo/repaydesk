using System.Globalization;

namespace Repaydesk.Gateway;

/// <summary>Money is integer kobo everywhere (ADR 0003). Only the edges speak decimal naira.</summary>
public static class Kobo
{
    public static string ToNaira(long kobo) =>
        (kobo / 100m).ToString("0.00", CultureInfo.InvariantCulture);

    public static long FromNaira(decimal naira)
    {
        var kobo = naira * 100m;
        if (kobo != decimal.Truncate(kobo))
            throw new ArgumentException($"{naira} naira is not a whole number of kobo");
        return (long)kobo;
    }
}

public sealed record Allocation(string LoanRef, long AmountKobo);

/// <summary>One approved match waiting in the outbox, with the line it came from.</summary>
public sealed record Posting(
    long Id,
    long ProposalId,
    string LineId,
    string IdempotencyKey,
    DateOnly ValueDate,
    long AmountKobo,
    IReadOnlyList<Allocation> Allocations,
    IReadOnlyList<string> ApprovedBy,
    int Attempts,
    string Channel,
    string BankReference)
{
    /// <summary>
    /// Fineract's external id for each allocation's repayment. One loan: the key itself, as the
    /// screens and ADR 0005 show it (<c>RD-P110</c>). A split: one repayment per loan,
    /// <c>RD-P110-1</c>, <c>RD-P110-2</c>, because Fineract's external ids are unique per transaction.
    /// </summary>
    public string ExternalIdFor(int index) =>
        Allocations.Count == 1 ? IdempotencyKey : $"{IdempotencyKey}-{index + 1}";
}

/// <summary>A repayment as core banking applied it: what went to principal, interest and the rest.</summary>
public sealed record CoreRepayment(
    string LoanRef,
    string ExternalId,
    long TransactionId,
    long PrincipalKobo,
    long InterestKobo,
    long ChargesKobo,
    long OverpaymentKobo)
{
    public long TotalKobo => PrincipalKobo + InterestKobo + ChargesKobo + OverpaymentKobo;
}

public sealed record JournalLine(string AccountCode, long DebitKobo, long CreditKobo, string Label);

public sealed record JournalEntry(DateOnly On, string Reference, string Memo, string Journal, IReadOnlyList<JournalLine> Lines)
{
    public bool Balanced => Lines.Sum(l => l.DebitKobo) == Lines.Sum(l => l.CreditKobo);
}

/// <summary>
/// Why a posting didn't go through. <see cref="Permanent"/> means retrying can't help (core banking
/// refused the repayment, or the approval doesn't hold): the posting fails and waits for a person.
/// Anything else (a timeout, a 5xx, a login problem) is retried with backoff, indefinitely.
/// </summary>
public sealed class PostingException(string message, bool permanent, Exception? inner = null) : Exception(message, inner)
{
    public bool Permanent { get; } = permanent;
}

public interface ICoreBanking
{
    /// <summary>The repayment posted under this external id, or null if there is none.</summary>
    Task<CoreRepayment?> FindRepaymentAsync(string loanRef, string externalId, CancellationToken ct);

    /// <summary>Posts and returns the repayment as applied. Callers look first: this always posts.</summary>
    Task<CoreRepayment> PostRepaymentAsync(string loanRef, DateOnly on, long amountKobo, string externalId,
        string channel, string note, CancellationToken ct);
}

public interface ILedger
{
    /// <summary>The ledger's name for the entry under this reference, posting it if it was left a draft.</summary>
    Task<string?> FindEntryAsync(string reference, CancellationToken ct);

    /// <summary>Posts the entry and returns its name. Callers look first: this always creates.</summary>
    Task<string> PostEntryAsync(JournalEntry entry, CancellationToken ct);
}
