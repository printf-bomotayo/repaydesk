using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Repaydesk.Gateway;

/// <summary>
/// Apache Fineract over its REST API, with the gateway's own login: it may read a loan and post a
/// repayment, nothing else. Repayments are idempotent through Fineract's transaction external id.
/// </summary>
public sealed class FineractCoreBanking : ICoreBanking
{
    readonly HttpClient _http;
    readonly SemaphoreSlim _typesLock = new(1, 1);
    Dictionary<string, long>? _paymentTypes;

    public FineractCoreBanking(HttpClient http, Settings s)
    {
        _http = http;
        _http.BaseAddress = new Uri(s.FineractUrl.TrimEnd('/') + "/");
        _http.Timeout = TimeSpan.FromSeconds(60);
        _http.DefaultRequestHeaders.Add("Fineract-Platform-TenantId", s.FineractTenant);
        _http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Basic",
            Convert.ToBase64String(Encoding.UTF8.GetBytes($"{s.FineractUsername}:{s.FineractPassword}")));
    }

    public async Task<CoreRepayment?> FindRepaymentAsync(string loanRef, string externalId, CancellationToken ct)
    {
        using var res = await Send(HttpMethod.Get,
            $"loans/external-id/{Uri.EscapeDataString(loanRef)}/transactions/external-id/{Uri.EscapeDataString(externalId)}", null, ct);
        if (res.StatusCode == HttpStatusCode.NotFound)
            return null;
        var tx = await Read(res, ct);
        if (tx["manuallyReversed"]?.GetValue<bool>() == true)
            throw new PostingException($"{externalId} on {loanRef} was posted and later reversed in Fineract", permanent: true);
        return ToRepayment(loanRef, externalId, tx);
    }

    public async Task<CoreRepayment> PostRepaymentAsync(string loanRef, DateOnly on, long amountKobo, string externalId,
        string channel, string note, CancellationToken ct)
    {
        var body = new JsonObject
        {
            ["dateFormat"] = "yyyy-MM-dd",
            ["locale"] = "en",
            ["transactionDate"] = on.ToString("yyyy-MM-dd"),
            ["transactionAmount"] = Kobo.ToNaira(amountKobo),
            ["externalId"] = externalId,
            ["paymentTypeId"] = await PaymentType(channel, ct),
            ["note"] = note,
        };
        using (var res = await Send(HttpMethod.Post,
                   $"loans/external-id/{Uri.EscapeDataString(loanRef)}/transactions?command=repayment", body, ct))
            await Read(res, ct);
        // The response carries only ids; the split comes from the transaction itself.
        return await FindRepaymentAsync(loanRef, externalId, ct)
               ?? throw new PostingException($"Fineract accepted {externalId} but can't find it", permanent: false);
    }

    async Task<long> PaymentType(string channel, CancellationToken ct)
    {
        if (_paymentTypes is null)
        {
            await _typesLock.WaitAsync(ct);
            try
            {
                if (_paymentTypes is null)
                {
                    using var res = await Send(HttpMethod.Get, "paymenttypes", null, ct);
                    var types = (await Read(res, ct)).AsArray();
                    _paymentTypes = types.ToDictionary(t => t!["name"]!.GetValue<string>(), t => t!["id"]!.GetValue<long>());
                }
            }
            finally { _typesLock.Release(); }
        }
        return _paymentTypes.TryGetValue(channel, out var id)
            ? id
            : throw new PostingException($"Fineract has no payment type {channel}", permanent: true);
    }

    static CoreRepayment ToRepayment(string loanRef, string externalId, JsonNode tx)
    {
        long K(string field) => Kobo.FromNaira(tx[field]?.GetValue<decimal>() ?? 0m);
        var r = new CoreRepayment(loanRef, externalId, tx["id"]!.GetValue<long>(),
            K("principalPortion"), K("interestPortion"), K("feeChargesPortion") + K("penaltyChargesPortion"),
            K("overpaymentPortion"));
        var amount = K("amount");
        return r.TotalKobo == amount
            ? r
            : throw new PostingException($"Fineract's split of {externalId} adds to {r.TotalKobo} kobo, not {amount}", permanent: true);
    }

    async Task<HttpResponseMessage> Send(HttpMethod method, string path, JsonObject? body, CancellationToken ct)
    {
        using var req = new HttpRequestMessage(method, path);
        if (body is not null)
            req.Content = JsonContent.Create(body);
        try
        {
            return await _http.SendAsync(req, ct);
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException && !ct.IsCancellationRequested)
        {
            throw new PostingException($"Fineract didn't answer: {e.Message}", permanent: false, e);
        }
    }

    /// <summary>
    /// The body of a successful response. A 5xx, a login failure or a missing privilege is the
    /// gateway's or Fineract's problem, so it is retried; any other 4xx is Fineract refusing this
    /// repayment (a closed loan, a date before disbursement), which needs a person.
    /// </summary>
    static async Task<JsonNode> Read(HttpResponseMessage res, CancellationToken ct)
    {
        var text = await res.Content.ReadAsStringAsync(ct);
        if (res.IsSuccessStatusCode)
            return JsonNode.Parse(text) ?? new JsonObject();
        var status = (int)res.StatusCode;
        var message = Describe(text);
        var systemic = status >= 500 || status == 401 || status == 429 ||
                       (status == 403 && message.Contains("privileges", StringComparison.OrdinalIgnoreCase));
        throw new PostingException($"Fineract {status}: {message}", permanent: !systemic);
    }

    static string Describe(string text)
    {
        try
        {
            var j = JsonNode.Parse(text);
            var errors = j?["errors"]?.AsArray().Select(e => e?["defaultUserMessage"]?.GetValue<string>()).Where(m => m is not null);
            var detail = errors is not null ? string.Join("; ", errors) : "";
            return detail.Length > 0 ? detail : j?["defaultUserMessage"]?.GetValue<string>() ?? text;
        }
        catch (JsonException)
        {
            return text.Length > 300 ? text[..300] : text;
        }
    }
}
