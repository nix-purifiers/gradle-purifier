# SPDX-License-Identifier: MIT
{
  pkgs,
  build,
  purifier,
}:
let
  producer =
    pkgs.runCommand "gradle-nix-input"
      {
        nativeBuildInputs = [ pkgs.jdk_headless ];
        passthru.projections = purifier.jvmProjections {
          repository = producer;
          inherit (pkgs) runCommand python3;
        };
      }
      ''
        mkdir -p classes
        cat > StringUtils.java <<'JAVA'
        package org.apache.commons.lang3;
        public final class StringUtils {
          public static boolean isEmpty(CharSequence value) {
            return value == null || value.length() == 0;
          }
          public static String capitalize(String value) {
            if (value == null || value.isEmpty()) return value;
            return Character.toUpperCase(value.charAt(0)) + value.substring(1);
          }
        }
        JAVA
        javac --release 8 -d classes StringUtils.java
        dir=$out/org/apache/commons/commons-lang3/0.0.1
        mkdir -p "$dir"
        jar --create --file "$dir/commons-lang3-0.0.1.jar" \
          --date=1980-01-01T00:00:02Z -C classes .
        cat > "$dir/commons-lang3-0.0.1.pom" <<'POM'
        <project xmlns="http://maven.apache.org/POM/4.0.0">
          <modelVersion>4.0.0</modelVersion><groupId>org.apache.commons</groupId>
          <artifactId>commons-lang3</artifactId><version>0.0.1</version>
        </project>
        POM
      '';
  original = builtins.fromJSON (builtins.readFile ./fixtures/java-library/gradle-purifier.lock.json);
  withoutProvided = removeAttrs (
    original
    // {
      artifacts = builtins.filter (
        artifact: !(pkgs.lib.hasInfix "/org/apache/commons/commons-lang3/" (artifact.url or artifact.path))
      ) original.artifacts;
    }
  ) [ "contentHash" ];
  lockFile = builtins.toFile "gradle-purifier.lock.json" (
    builtins.toJSON (
      withoutProvided
      // {
        contentHash = "sha256:${builtins.hashString "sha256" (builtins.toJSON withoutProvided)}";
      }
    )
  );
  consumer = build {
    src = ./fixtures/java-library;
    inherit lockFile;
    projectDependencies = [ producer ];
    doCheck = true;
  };
in
pkgs.runCommand "gradle-nix-composition"
  {
    nativeBuildInputs = [ pkgs.python3 ];
    passthru = { inherit producer consumer; };
  }
  ''
    export CATALOG=${producer.projections.maven.catalog}
    export REPOSITORY=${consumer.packages.greeting.maven}
    export PUBLICATION=${consumer.publication}/repository
    export DOWNLOADS=${consumer.repository}
    export RUNTIME=${consumer.packages.greeting.runtime}
    python - <<'PY'
    import json, os, pathlib, xml.etree.ElementTree as ET
    catalog = json.load(open(os.environ['CATALOG']))
    selected = next(m['version'] for m in catalog['modules'] if m['name'] == 'commons-lang3')
    root = ET.parse(next(pathlib.Path(os.environ['REPOSITORY']).rglob('*.pom'))).getroot()
    ns = {'m': 'http://maven.apache.org/POM/4.0.0'}
    dependency = next(d for d in root.findall('m:dependencies/m:dependency', ns)
                      if d.findtext('m:artifactId', namespaces=ns) == 'commons-lang3')
    assert dependency.findtext('m:version', namespaces=ns) == selected
    # commons-text's parent imports this BOM. Preserve its already-locked
    # module sidecar without promoting the platform into the runtime graph.
    relative = 'org/junit/junit-bom/5.11.2/junit-bom-5.11.2'
    published = pathlib.Path(os.environ['PUBLICATION'])
    captured = pathlib.Path(os.environ['DOWNLOADS']) / 'https/repo.maven.apache.org/maven2'
    assert (published / (relative + '.module')).read_bytes() == (captured / (relative + '.module')).read_bytes()
    assert 'published-with-gradle-metadata' in (published / (relative + '.pom')).read_text()
    runtime = json.loads((pathlib.Path(os.environ['RUNTIME']) / 'purifier-runtime-modules.json').read_text())
    assert not any(m['group'] == 'org.junit' and m['name'] == 'junit-bom' for m in runtime['modules'])
    PY
    touch $out
  ''
