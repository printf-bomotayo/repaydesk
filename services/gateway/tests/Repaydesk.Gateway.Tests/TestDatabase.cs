using System.Collections.Concurrent;
using System.Text.Json;
using Npgsql;

namespace Repaydesk.Gateway.Tests;

/// <summary>
/// A throwaway schema in a real Postgres with every Repaydesk migration applied, so the tests see
/// the real constraints and triggers. Set REPAYDESK_TEST_DATABASE_URL (CI does; locally
/// <c>python scripts/dev.py gateway</c> points it at the compose database). Without it, these skip.
/// </summary>
public sealed class TestDatabase : IAsyncDisposable
{
    readonly string _admin;
    readonly string _schema;
    int _lines;

    public NpgsqlDataSource Source { get; }

    TestDatabase(string admin, string schema, NpgsqlDataSource source)
    {
        _admin = admin;
        _schema = schema;
        Source = source;
    }

    public static async Task<TestDatabase> CreateAsync()
    {
        var url = Environment.GetEnvironmentVariable("REPAYDESK_TEST_DATABASE_URL");
        Assert.SkipWhen(string.IsNullOrEmpty(url), "REPAYDESK_TEST_DATABASE_URL is not set");
        var admin = Settings.ToConnectionString(url!);
        var schema = $"g_{Guid.NewGuid():N}"[..14];
        await using (var conn = new NpgsqlConnection(admin))
        {
            await conn.OpenAsync();
            await new NpgsqlCommand($"CREATE SCHEMA {schema}", conn).ExecuteNonQueryAsync();
        }
        var source = NpgsqlDataSource.Create(new NpgsqlConnectionStringBuilder(admin) { SearchPath = schema }.ConnectionString);
        var db = new TestDatabase(admin, schema, source);
        await using var c = await source.OpenConnectionAsync();
        foreach (var file in Directory.GetFiles(Migrations(), "*.sql").Order())
            await new NpgsqlCommand(await File.ReadAllTextAsync(file), c).ExecuteNonQueryAsync();
        await new NpgsqlCommand("""
            INSERT INTO staff (id, name, role, limit_kobo, password_hash) VALUES
              ('amaka', 'Amaka Nwosu', 'officer', 50000000, 'x'),
              ('kunle', 'Kunle Bello', 'officer', 50000000, 'x'),
              ('tunde', 'Tunde Adeyemi', 'supervisor', 500000000, 'x'),
              ('hadiza', 'Hadiza Lawal', 'auditor', 0, 'x')
            """, c).ExecuteNonQueryAsync();
        return db;
    }

    /// <summary>The migrations live with the Python service, which owns the schema.</summary>
    static string Migrations()
    {
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir is not null; dir = dir.Parent)
        {
            var candidate = Path.Combine(dir.FullName, "services", "core", "src", "repaydesk", "migrations");
            if (Directory.Exists(candidate))
                return candidate;
        }
        throw new DirectoryNotFoundException("services/core/src/repaydesk/migrations");
    }

    /// <summary>
    /// What the API does when people approve a match (approvals.py): the line and proposal marked
    /// approved, a decision per approver with their limit, and the outbox row, all at once.
    /// </summary>
    public async Task<(long OutboxId, string LineId, string Key)> ApprovedAsync(long amount, string[]? approvers = null,
        (string Loan, long Amount)[]? allocations = null, string channel = "bank_csv")
    {
        approvers ??= amount > Settings.DefaultTwoPersonAboveKobo ? ["amaka", "tunde"] : ["amaka"];
        allocations ??= [("LN-2026-000001", amount)];
        var json = JsonSerializer.Serialize(allocations.Select(a => new { loan_ref = a.Loan, amount_kobo = a.Amount }));
        var n = Interlocked.Increment(ref _lines);
        var lineId = $"{channel}:REF{n:0000}";
        await using var c = await Source.OpenConnectionAsync();
        await using var tx = await c.BeginTransactionAsync();
        await Exec(c, """
            INSERT INTO payment_lines (id, channel, source_account, value_date, amount_kobo, narration, bank_reference, dedupe_key, status)
            VALUES (@id, @channel, '1023456789', DATE '2026-10-06', @amount, 'TRF', @ref, @id, 'approved')
            """, ("id", lineId), ("channel", channel), ("amount", amount), ("ref", $"REF{n:0000}"));
        var pid = (long)(await Scalar(c, """
            INSERT INTO proposals (line_id, kind, proposed_by, confidence, allocations, status)
            VALUES (@line, 'match', 'rule:test', 1, @alloc::jsonb, 'approved') RETURNING id
            """, ("line", lineId), ("alloc", json)))!;
        foreach (var who in approvers)
            await Exec(c, """
                INSERT INTO decisions (proposal_id, staff_id, decision, limit_kobo)
                SELECT @pid, id, 'approve', limit_kobo FROM staff WHERE id = @who
                """, ("pid", pid), ("who", who));
        var key = $"RD-P{pid}";
        var outboxId = (long)(await Scalar(c, """
            INSERT INTO posting_outbox (proposal_id, line_id, idempotency_key, value_date, amount_kobo, allocations, approved_by)
            VALUES (@pid, @line, @key, DATE '2026-10-06', @amount, @alloc::jsonb, @by) RETURNING id
            """, ("pid", pid), ("line", lineId), ("key", key), ("amount", amount), ("alloc", json), ("by", approvers)))!;
        await tx.CommitAsync();
        return (outboxId, lineId, key);
    }

    public async Task<object?> ScalarAsync(string sql, params (string, object)[] args)
    {
        await using var c = await Source.OpenConnectionAsync();
        return await Scalar(c, sql, args);
    }

    public async Task ExecAsync(string sql, params (string, object)[] args)
    {
        await using var c = await Source.OpenConnectionAsync();
        await Exec(c, sql, args);
    }

    static async Task<object?> Scalar(NpgsqlConnection c, string sql, params (string Name, object Value)[] args)
    {
        await using var cmd = new NpgsqlCommand(sql, c);
        foreach (var (name, value) in args)
            cmd.Parameters.AddWithValue(name, value);
        return await cmd.ExecuteScalarAsync();
    }

    static async Task Exec(NpgsqlConnection c, string sql, params (string, object)[] args) => await Scalar(c, sql, args);

    public async ValueTask DisposeAsync()
    {
        await Source.DisposeAsync();
        await using var conn = new NpgsqlConnection(_admin);
        await conn.OpenAsync();
        await new NpgsqlCommand($"DROP SCHEMA {_schema} CASCADE", conn).ExecuteNonQueryAsync();
    }
}

/// <summary>Core banking as a dictionary of external ids, splitting each repayment 80/20 principal/interest.</summary>
public sealed class FakeCore : ICoreBanking
{
    public ConcurrentDictionary<string, CoreRepayment> Posted { get; } = new();
    public int Posts;
    public int Duplicates;
    /// <summary>Thrown by the next post instead of posting, if set.</summary>
    public PostingException? FailNextPost;
    long _ids = 1000;

    public Task<CoreRepayment?> FindRepaymentAsync(string loanRef, string externalId, CancellationToken ct) =>
        Task.FromResult(Posted.TryGetValue(externalId, out var r) ? r : null);

    public async Task<CoreRepayment> PostRepaymentAsync(string loanRef, DateOnly on, long amountKobo, string externalId,
        string channel, string note, CancellationToken ct)
    {
        await Task.Yield();
        if (Interlocked.Exchange(ref FailNextPost, null) is { } e)
            throw e;
        Interlocked.Increment(ref Posts);
        var principal = amountKobo * 4 / 5;
        var r = new CoreRepayment(loanRef, externalId, Interlocked.Increment(ref _ids), principal, amountKobo - principal, 0, 0);
        if (!Posted.TryAdd(externalId, r))
            Interlocked.Increment(ref Duplicates);
        return r;
    }
}

public sealed class FakeLedger : ILedger
{
    public ConcurrentDictionary<string, JournalEntry> Entries { get; } = new();
    public int Posts;
    public PostingException? FailNextPost;

    public Task<string?> FindEntryAsync(string reference, CancellationToken ct) =>
        Task.FromResult(Entries.ContainsKey(reference) ? $"RPY/{reference}" : null);

    public async Task<string> PostEntryAsync(JournalEntry entry, CancellationToken ct)
    {
        await Task.Yield();
        if (Interlocked.Exchange(ref FailNextPost, null) is { } e)
            throw e;
        Interlocked.Increment(ref Posts);
        Assert.True(Entries.TryAdd(entry.Reference, entry), $"{entry.Reference} entered twice");
        return $"RPY/{entry.Reference}";
    }
}
