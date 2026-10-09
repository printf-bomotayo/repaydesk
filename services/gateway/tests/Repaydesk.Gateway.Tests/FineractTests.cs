using System.Net;
using System.Text;

namespace Repaydesk.Gateway.Tests;

/// <summary>
/// The Fineract client against canned responses in Fineract's own shapes: what counts as "already
/// there", how the split is read, and which refusals are retried and which need a person.
/// </summary>
public class FineractTests
{
    sealed class Stub(Func<HttpRequestMessage, (HttpStatusCode, string)> answer) : HttpMessageHandler
    {
        public List<string> Calls { get; } = [];

        protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken ct)
        {
            Calls.Add($"{request.Method} {request.RequestUri!.PathAndQuery}");
            var (status, body) = answer(request);
            return Task.FromResult(new HttpResponseMessage(status) { Content = new StringContent(body, Encoding.UTF8, "application/json") });
        }
    }

    static readonly Settings S = new("", "http://fineract.test/fineract-provider/api/v1", "default", "repaydesk-gateway",
        "x", "", "", "", "", Settings.DefaultTwoPersonAboveKobo, TimeSpan.FromSeconds(15));

    const string Transaction = """
        {"id": 4301, "externalId": "RD-P110", "amount": 60500.0, "principalPortion": 50000.0,
         "interestPortion": 10500.0, "feeChargesPortion": 0, "penaltyChargesPortion": 0, "overpaymentPortion": 0,
         "manuallyReversed": false}
        """;

    static string Error(string message) =>
        $$"""{"developerMessage": "x", "errors": [{"defaultUserMessage": "{{message}}"}]}""";

    [Fact]
    public async Task Finds_a_posted_repayment_by_its_key_and_reads_the_split()
    {
        var stub = new Stub(_ => (HttpStatusCode.OK, Transaction));
        var core = new FineractCoreBanking(new HttpClient(stub), S);
        var r = await core.FindRepaymentAsync("LN-2026-001209", "RD-P110", TestContext.Current.CancellationToken);
        Assert.Equal(new CoreRepayment("LN-2026-001209", "RD-P110", 4301, 50_000_00, 10_500_00, 0, 0), r);
        Assert.Equal(["GET /fineract-provider/api/v1/loans/external-id/LN-2026-001209/transactions/external-id/RD-P110"], stub.Calls);
    }

    [Fact]
    public async Task Nothing_under_the_key_is_null_not_an_error()
    {
        var core = new FineractCoreBanking(new HttpClient(new Stub(_ => (HttpStatusCode.NotFound, Error("not available")))), S);
        Assert.Null(await core.FindRepaymentAsync("LN-1", "RD-P1", TestContext.Current.CancellationToken));
    }

    [Fact]
    public async Task Posts_with_the_channels_payment_type_then_reads_back_what_was_applied()
    {
        var posted = false;
        var stub = new Stub(req => req.RequestUri!.AbsolutePath switch
        {
            var p when p.EndsWith("/paymenttypes") => (HttpStatusCode.OK, """[{"id": 3, "name": "bank_csv"}, {"id": 5, "name": "virtual_account"}]"""),
            var p when p.EndsWith("/transactions") && req.Method == HttpMethod.Post =>
                (posted = true) && req.Content!.ReadAsStringAsync().Result.Contains("\"paymentTypeId\":5")
                    && req.Content.ReadAsStringAsync().Result.Contains("\"transactionAmount\":\"60500.00\"")
                    ? (HttpStatusCode.OK, """{"resourceId": 4301}""")
                    : (HttpStatusCode.BadRequest, Error("unexpected body")),
            _ => posted ? (HttpStatusCode.OK, Transaction) : (HttpStatusCode.NotFound, ""),
        });
        var core = new FineractCoreBanking(new HttpClient(stub), S);
        var r = await core.PostRepaymentAsync("LN-2026-001209", new DateOnly(2026, 10, 6), 60_500_00, "RD-P110", "virtual_account", "n", TestContext.Current.CancellationToken);
        Assert.Equal(4301, r.TransactionId);
        Assert.Contains("POST /fineract-provider/api/v1/loans/external-id/LN-2026-001209/transactions?command=repayment", stub.Calls);
    }

    [Theory]
    [InlineData(HttpStatusCode.Forbidden, "Loan: 1209 is not active.", true)]
    [InlineData(HttpStatusCode.BadRequest, "The parameter `transactionDate` cannot be before disbursement.", true)]
    [InlineData(HttpStatusCode.Forbidden, "The user associated with credentials passed on this request does not have sufficient privileges to perform this action.", false)]
    [InlineData(HttpStatusCode.Unauthorized, "Unauthenticated. Please login.", false)]
    [InlineData(HttpStatusCode.InternalServerError, "boom", false)]
    [InlineData(HttpStatusCode.ServiceUnavailable, "starting", false)]
    public async Task Refusals_need_a_person_and_outages_are_retried(HttpStatusCode status, string message, bool permanent)
    {
        var core = new FineractCoreBanking(new HttpClient(new Stub(_ => (status, Error(message)))), S);
        var e = await Assert.ThrowsAsync<PostingException>(() => core.FindRepaymentAsync("LN-1", "RD-P1", TestContext.Current.CancellationToken));
        Assert.Equal(permanent, e.Permanent);
        Assert.Contains(message, e.Message);
    }

    [Fact]
    public async Task No_answer_is_retried()
    {
        var core = new FineractCoreBanking(new HttpClient(new Stub(_ => throw new HttpRequestException("connection refused"))), S);
        var e = await Assert.ThrowsAsync<PostingException>(() => core.FindRepaymentAsync("LN-1", "RD-P1", TestContext.Current.CancellationToken));
        Assert.False(e.Permanent);
    }

    [Fact]
    public async Task A_reversed_repayment_is_not_taken_as_posted()
    {
        var core = new FineractCoreBanking(new HttpClient(new Stub(_ =>
            (HttpStatusCode.OK, Transaction.Replace("\"manuallyReversed\": false", "\"manuallyReversed\": true")))), S);
        var e = await Assert.ThrowsAsync<PostingException>(() => core.FindRepaymentAsync("LN-1", "RD-P110", TestContext.Current.CancellationToken));
        Assert.True(e.Permanent);
    }
}
