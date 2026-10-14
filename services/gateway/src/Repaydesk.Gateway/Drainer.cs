using System.Diagnostics;
using System.Threading.Channels;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using Npgsql;

namespace Repaydesk.Gateway;

public static class Telemetry
{
    public static readonly ActivitySource Source = new("Repaydesk.Gateway");
}

/// <summary>
/// Proof of life for the container's health check: the drain loop touches a file every time round,
/// and <c>--healthy</c> checks it is recent. No web server needed for one yes-or-no question.
/// </summary>
public static class Heartbeat
{
    static readonly string File = Path.Combine(Path.GetTempPath(), "repaydesk-gateway.alive");

    public static void Beat() => System.IO.File.WriteAllText(File, DateTimeOffset.UtcNow.ToString("O"));

    public static bool Healthy(TimeSpan within) =>
        System.IO.File.Exists(File) && DateTime.UtcNow - System.IO.File.GetLastWriteTimeUtc(File) < within;
}

/// <summary>
/// Drains both outboxes, postings and messages, until nothing is ready, then waits: for Postgres to
/// say something arrived (<c>LISTEN posting_outbox</c> and <c>message_outbox</c>, raised by
/// triggers), or for the poll interval, which also picks up retries and messages held for contact
/// hours.
/// </summary>
public sealed class Drainer(NpgsqlDataSource db, Outbox outbox, MessageOutbox messages, Settings settings, ILogger<Drainer> log)
    : BackgroundService
{
    protected override async Task ExecuteAsync(CancellationToken stop)
    {
        var wake = Channel.CreateBounded<bool>(new BoundedChannelOptions(1) { FullMode = BoundedChannelFullMode.DropWrite });
        var listening = Listen(wake.Writer, stop);
        log.LogInformation("Gateway started; polling every {Poll}s", settings.Poll.TotalSeconds);
        while (!stop.IsCancellationRequested)
        {
            Heartbeat.Beat();
            try
            {
                while (await outbox.PostNextAsync(stop) != Outcome.Nothing | await messages.SendNextAsync(stop) != Outcome.Nothing)
                    Heartbeat.Beat();
            }
            catch (Exception e) when (!stop.IsCancellationRequested)
            {
                log.LogError(e, "Draining the outbox failed; trying again shortly");
            }
            using var wait = CancellationTokenSource.CreateLinkedTokenSource(stop);
            wait.CancelAfter(settings.Poll);
            try
            {
                await wake.Reader.ReadAsync(wait.Token);
            }
            catch (OperationCanceledException) when (!stop.IsCancellationRequested)
            {
                // the poll interval passed
            }
        }
        await listening;
    }

    async Task Listen(ChannelWriter<bool> wake, CancellationToken stop)
    {
        while (!stop.IsCancellationRequested)
        {
            try
            {
                await using var conn = await db.OpenConnectionAsync(stop);
                conn.Notification += (_, _) => wake.TryWrite(true);
                await using (var cmd = new NpgsqlCommand("LISTEN posting_outbox; LISTEN message_outbox", conn))
                    await cmd.ExecuteNonQueryAsync(stop);
                wake.TryWrite(true); // anything that arrived while we weren't listening
                while (true)
                    await conn.WaitAsync(stop);
            }
            catch (OperationCanceledException) when (stop.IsCancellationRequested)
            {
                return;
            }
            catch (Exception e)
            {
                log.LogWarning("Listening for new postings and messages failed ({Error}); polling until it reconnects", e.Message);
                try { await Task.Delay(TimeSpan.FromSeconds(5), stop); }
                catch (OperationCanceledException) { return; }
            }
        }
    }
}
