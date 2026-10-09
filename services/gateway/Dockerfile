# The posting gateway. Build with the SDK, run on the runtime image only (no web server: it has
# no port, just a loop over the outbox and a heartbeat file for the health check).
FROM mcr.microsoft.com/dotnet/sdk:10.0 AS build
WORKDIR /src
COPY src/Repaydesk.Gateway/Repaydesk.Gateway.csproj src/Repaydesk.Gateway/
RUN dotnet restore src/Repaydesk.Gateway/Repaydesk.Gateway.csproj
COPY src/ src/
RUN dotnet publish src/Repaydesk.Gateway/Repaydesk.Gateway.csproj -c Release -o /app --no-restore

FROM mcr.microsoft.com/dotnet/runtime:10.0
WORKDIR /app
COPY --from=build /app .
USER app
ENTRYPOINT ["dotnet", "Repaydesk.Gateway.dll"]
