# SPDX-License-Identifier: MIT
# Replay the actual prefetch result with a cold native cache inside the Nix
# sandbox. This check invokes the sample's ordinary wrapper, not purifierBuild.
{
  pkgs,
  src,
  jdk,
  javaHome,
  distribution,
  library,
  repository,
  properties,
  wrapperHome,
}:
pkgs.runCommand "petclinic-ordinary-offline"
  {
    # Gradle's file-lock listener needs loopback UDP even for offline builds.
    ${if pkgs.stdenv.hostPlatform.isDarwin then "__darwinAllowLocalNetworking" else null} = true;
    nativeBuildInputs = [ pkgs.python3 ];
    # Preserve the realized closure named by the prefetch properties/symlinks.
    buildInputs = [
      jdk
      distribution
      library
      repository
    ];
  }
  ''
    export HOME="$TMPDIR/home"
    export GRADLE_USER_HOME="$TMPDIR/gradle-home"
    export JAVA_HOME=${pkgs.lib.escapeShellArg javaHome}
    export JAVA_TOOL_OPTIONS="-Dhttp.proxyHost=127.0.0.1 -Dhttp.proxyPort=9 -Dhttps.proxyHost=127.0.0.1 -Dhttps.proxyPort=9"
    mkdir -p "$HOME" "$GRADLE_USER_HOME/init.d" project
    cp -R ${src}/. project/
    chmod -R u+w project
    cp ${library}/user.init.gradle "$GRADLE_USER_HOME/init.d/gradle-purifier.gradle"
    cp -R ${wrapperHome} "$GRADLE_USER_HOME/wrapper"
    chmod -R u+w "$GRADLE_USER_HOME/wrapper"
    mkdir -p project/.gradle/purifier
    sed "s|@PROJECT@|$PWD/project|g" ${properties} > project/.gradle/purifier/prefetch.properties
    cd project
    ./gradlew --no-daemon --info clean test bootJar
    test ! -d "$GRADLE_USER_HOME/caches/modules-2/files-2.1" || \
      test -z "$(ls -A "$GRADLE_USER_HOME/caches/modules-2/files-2.1")"
    mkdir -p "$out/test-results"
    python - "$out" <<'PY'
    import hashlib, json, shutil, sys
    from pathlib import Path
    import xml.etree.ElementTree as ET
    reports = list(Path('build/test-results/test').glob('TEST-*.xml'))
    assert reports, 'ordinary wrapper produced no test reports'
    counts = {'tests': 0, 'failures': 0, 'errors': 0, 'skipped': 0}
    for report in reports:
        suite = ET.parse(report).getroot()
        for key in counts:
            counts[key] += int(suite.get(key, 0))
        shutil.copy2(report, Path(sys.argv[1]) / 'test-results' / report.name)
    assert counts['tests'] > counts['skipped'] and counts['failures'] == counts['errors'] == 0, counts
    jars = list(Path('build/libs').glob('*.jar'))
    assert jars, 'ordinary wrapper produced no JAR'
    receipt = {'tests': counts, 'downloadCacheEmpty': True,
               'jars': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in jars}}
    (Path(sys.argv[1]) / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
    PY
  ''
