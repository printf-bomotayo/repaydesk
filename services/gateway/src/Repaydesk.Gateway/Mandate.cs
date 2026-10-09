namespace Repaydesk.Gateway;

public sealed record ProposalState(string Status, string Kind, IReadOnlyList<Allocation> Allocations);

public sealed record LineState(string Status, long AmountKobo);

/// <summary>A person's decision on the proposal, with their role and their limit when they decided.</summary>
public sealed record Decision(string StaffId, string Role, string Choice, long LimitKobo);

/// <summary>
/// The gateway's own check of the approval rules (ADR 0005) before money moves. The API applied
/// them when people decided; the gateway doesn't take the outbox row's word for it, and checks the
/// decisions themselves. An outbox row written by anything else, or one that disagrees with its
/// proposal, is refused and never posted (ADR 0002).
/// </summary>
public static class Mandate
{
    static readonly HashSet<string> MayApprove = ["officer", "supervisor"];

    /// <returns>Null if the posting may go ahead; otherwise why not, in a sentence for the audit log.</returns>
    public static string? Refusal(Posting posting, ProposalState? proposal, LineState? line,
        IReadOnlyList<Decision> decisions, long twoPersonAboveKobo)
    {
        if (proposal is null)
            return "the proposal doesn't exist";
        if (proposal.Kind != "match")
            return $"the proposal is an {proposal.Kind}, which moves no money";
        if (proposal.Status != "approved")
            return $"the proposal is {proposal.Status}, not approved";
        if (line is null)
            return "the payment line doesn't exist";
        if (line.Status != "approved")
            return $"the payment line is {line.Status}, not approved";
        if (line.AmountKobo != posting.AmountKobo)
            return $"the posting is for {posting.AmountKobo} kobo but the line is {line.AmountKobo}";

        if (posting.Allocations.Count == 0)
            return "the posting has no loans";
        if (posting.Allocations.Any(a => a.AmountKobo <= 0))
            return "a loan's amount is not positive";
        if (posting.Allocations.Select(a => a.LoanRef).Distinct().Count() != posting.Allocations.Count)
            return "a loan appears twice";
        if (posting.Allocations.Sum(a => a.AmountKobo) != posting.AmountKobo)
            return "the loans' amounts don't add up to the posting";
        if (!posting.Allocations.SequenceEqual(proposal.Allocations))
            return "the posting's loans and amounts differ from what was approved";

        if (decisions.Any(d => d.Choice == "reject"))
            return "someone rejected this proposal";
        var approvers = decisions.Where(d => d.Choice == "approve").ToList();
        if (approvers.Any(d => !MayApprove.Contains(d.Role)))
            return "an approval came from someone whose role can't approve";
        var named = posting.ApprovedBy.ToHashSet();
        if (named.Count != posting.ApprovedBy.Count || !named.SetEquals(approvers.Select(d => d.StaffId)))
            return "the people named on the posting aren't the people who approved";

        var needed = posting.AmountKobo > twoPersonAboveKobo ? 2 : 1;
        if (approvers.Count < needed)
            return $"needs {needed} approval{(needed == 1 ? "" : "s")}, has {approvers.Count}";
        if (approvers.Max(d => d.LimitKobo) < posting.AmountKobo)
            return "no approver's limit covers the amount";
        return null;
    }
}
