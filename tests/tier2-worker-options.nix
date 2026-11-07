# Exercise the actual effective SDK's process-isolated worker in the Nix sandbox.
{
  pkgs,
  distribution,
  javaHome,
  jdk,
  derived,
}:
pkgs.runCommand "gradle-worker-transport-observation"
  {
    ${if pkgs.stdenv.hostPlatform.isDarwin then "__darwinAllowLocalNetworking" else null} = true;
    nativeBuildInputs = [ pkgs.python3 ];
    buildInputs = [ jdk ];
  }
  ''
    export HOME="$TMPDIR/home"
    export GRADLE_USER_HOME="$TMPDIR/gradle-home"
    export JAVA_HOME=${pkgs.lib.escapeShellArg javaHome}
    mkdir -p "$HOME" project "$out"
    cp ${./worker-transport/build.gradle} project/build.gradle
    cp ${./worker-transport/settings.gradle} project/settings.gradle
    cd project
    export JAVA_TOOL_OPTIONS="-Djava.net.preferIPv4Stack=true -Dpurifier.probe.unrelated=parent-only"
    export PURIFIER_PROBE_UNRELATED=parent-only
    export LANG=C.UTF-8
    ${distribution}/bin/gradle --no-daemon --offline --console=plain --max-workers=2 inherited > "$out/worker.log" 2>&1 || {
      head -c 16384 "$out/worker.log"
      exit 1
    }
    python - "$out" ${pkgs.lib.escapeShellArg (toString distribution)} ${pkgs.lib.escapeShellArg javaHome} <<'PY'
    import json, sys
    from pathlib import Path
    output = Path(sys.argv[1])
    lines = (output / 'worker.log').read_text().splitlines()
    prefix = 'PURIFIER_WORKER_TRANSPORT '
    rows = [json.loads(line[len(prefix):]) for line in lines if line.startswith(prefix)]
    assert len(rows) == 1, rows
    row = rows[0]
    selected_home = Path(sys.argv[3]).resolve()
    observed_home = Path(row['javaHome']).resolve()
    assert observed_home == selected_home or selected_home in observed_home.parents, row
    assert row['preferIPv4Stack'] == ${if derived then "'true'" else "None"}, row
    assert row['javaToolOptions'] is None and row['unrelatedProperty'] is None, row
    assert row['unrelatedEnvironment'] is None and row['locale'] == 'C.UTF-8', row
    origins = [line for line in lines if line.startswith('PURIFIER_COMPONENT_ORIGIN ')]
    assert len(origins) == 1 and sys.argv[2] + '/lib/plugins/' in origins[0], origins
    (output / 'receipt.json').write_text(json.dumps({
        'worker': row, 'componentOrigin': origins[0],
        'effectiveDistribution': sys.argv[2], 'derived': ${if derived then "True" else "False"}
    }, indent=2) + '\n')
    PY
  ''
