using System.Net.Http.Json;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace Repaydesk.Gateway;

/// <summary>
/// Odoo Community as the finance ledger, over JSON-RPC. One journal entry per posting, found again
/// by its reference (the idempotency key), so a retry finds what an earlier attempt left.
/// </summary>
public sealed class OdooLedger : ILedger
{
    readonly HttpClient _http;
    readonly Settings _s;
    readonly SemaphoreSlim _lock = new(1, 1);
    readonly Dictionary<string, long> _accounts = [];
    readonly Dictionary<string, long> _journals = [];
    long? _uid;
    long _ids;

    public OdooLedger(HttpClient http, Settings s)
    {
        _http = http;
        _http.BaseAddress = new Uri(s.OdooUrl.TrimEnd('/') + "/");
        _http.Timeout = TimeSpan.FromSeconds(60);
        _s = s;
    }

    public async Task<string?> FindEntryAsync(string reference, CancellationToken ct)
    {
        var found = (await Execute("account.move", "search_read",
            [new JsonArray(new JsonArray("ref", "=", reference), new JsonArray("state", "!=", "cancel"))],
            new JsonObject { ["fields"] = new JsonArray("name", "state"), ["limit"] = 1 }, ct)).AsArray();
        if (found.Count == 0)
            return null;
        var move = found[0]!;
        if (move["state"]!.GetValue<string>() == "draft")
            return await Post(move["id"]!.GetValue<long>(), ct); // an earlier attempt stopped between create and post
        return move["name"]!.GetValue<string>();
    }

    public async Task<string> PostEntryAsync(JournalEntry entry, CancellationToken ct)
    {
        var lines = new JsonArray();
        foreach (var l in entry.Lines)
            lines.Add(new JsonArray(0, 0, new JsonObject
            {
                ["account_id"] = await Account(l.AccountCode, ct),
                ["name"] = l.Label,
                ["debit"] = l.DebitKobo / 100m,
                ["credit"] = l.CreditKobo / 100m,
            }));
        var id = (await Execute("account.move", "create", [new JsonObject
        {
            ["move_type"] = "entry",
            ["journal_id"] = await Journal(entry.Journal, ct),
            ["date"] = entry.On.ToString("yyyy-MM-dd"),
            ["ref"] = entry.Reference,
            ["narration"] = entry.Memo,
            ["line_ids"] = lines,
        }], null, ct)).GetValue<long>();
        return await Post(id, ct);
    }

    async Task<string> Post(long moveId, CancellationToken ct)
    {
        await Execute("account.move", "action_post", [new JsonArray(moveId)], null, ct);
        var read = await Execute("account.move", "read", [new JsonArray(moveId)],
            new JsonObject { ["fields"] = new JsonArray("name") }, ct);
        return read[0]!["name"]!.GetValue<string>();
    }

    Task<long> Account(string code, CancellationToken ct) => Lookup(_accounts, "account.account", code, ct);

    Task<long> Journal(string code, CancellationToken ct) => Lookup(_journals, "account.journal", code, ct);

    async Task<long> Lookup(Dictionary<string, long> cache, string model, string code, CancellationToken ct)
    {
        lock (cache)
            if (cache.TryGetValue(code, out var known))
                return known;
        var found = (await Execute(model, "search", [new JsonArray(new JsonArray("code", "=", code))],
            new JsonObject { ["limit"] = 1 }, ct)).AsArray();
        if (found.Count == 0)
            throw new PostingException($"the ledger has no {model} {code}; run the seed to set it up", permanent: true);
        var id = found[0]!.GetValue<long>();
        lock (cache)
            cache[code] = id;
        return id;
    }

    async Task<long> Uid(CancellationToken ct)
    {
        if (_uid is { } uid)
            return uid;
        await _lock.WaitAsync(ct);
        try
        {
            var res = await Rpc("common", "login", [_s.OdooDatabase, _s.OdooUsername, _s.OdooPassword], ct);
            if (res.GetValueKind() != JsonValueKind.Number)
                throw new PostingException("the ledger refused the gateway's login", permanent: false);
            _uid = res.GetValue<long>();
            return _uid.Value;
        }
        finally { _lock.Release(); }
    }

    async Task<JsonNode> Execute(string model, string method, JsonArray args, JsonObject? kwargs, CancellationToken ct)
    {
        var uid = await Uid(ct);
        return await Rpc("object", "execute_kw",
            [_s.OdooDatabase, uid, _s.OdooPassword, model, method, args, kwargs ?? new JsonObject()], ct);
    }

    async Task<JsonNode> Rpc(string service, string method, JsonArray args, CancellationToken ct)
    {
        var payload = new JsonObject
        {
            ["jsonrpc"] = "2.0",
            ["method"] = "call",
            ["id"] = Interlocked.Increment(ref _ids),
            ["params"] = new JsonObject { ["service"] = service, ["method"] = method, ["args"] = args },
        };
        JsonNode? res;
        try
        {
            using var http = await _http.PostAsync("jsonrpc", JsonContent.Create(payload), ct);
            if ((int)http.StatusCode >= 500)
                throw new PostingException($"the ledger answered {(int)http.StatusCode}", permanent: false);
            http.EnsureSuccessStatusCode();
            res = JsonNode.Parse(await http.Content.ReadAsStringAsync(ct));
        }
        catch (Exception e) when (e is HttpRequestException or TaskCanceledException or JsonException && !ct.IsCancellationRequested)
        {
            throw new PostingException($"the ledger didn't answer: {e.Message}", permanent: false, e);
        }
        if (res?["error"] is { } error)
        {
            var name = error["data"]?["name"]?.GetValue<string>() ?? "";
            var message = error["data"]?["message"]?.GetValue<string>() ?? error["message"]?.GetValue<string>() ?? "unknown error";
            // A refused login is configuration, worth retrying once fixed; Odoo refusing the entry
            // itself (validation, a locked period) needs a person.
            var systemic = name.EndsWith("AccessDenied") || name.EndsWith("SessionExpiredException");
            if (systemic)
                _uid = null;
            throw new PostingException($"the ledger refused: {message}", permanent: !systemic);
        }
        return res?["result"] ?? throw new PostingException("the ledger sent no result", permanent: false);
    }
}
