# Native legacy Maven publication controls

These controls target the Gradle 6.9.4 `maven` plugin and its original
`uploadArchives` task, with manager Java 11 and compiler/asserting runtime Java 8.
They do not establish a Gradle version range or a general JVM compatibility claim.
The existing epoch fixture's disabled JUnit task is not credited as execution.

`lifecycle.py` retains the unchanged fixture through native publication, lock and
relocation, cold network-isolated replay, Nix tests, and original-task deployment
with compilation forbidden. `metadata.py` adds native POM callbacks and a real
loopback dependency origin, stops it after prefetch, and compares every deployed
producer and dependency JAR/POM with the authoritative Nix projection. Fresh
Gradle and Maven consumers resolve only the deployment for project dependencies.
Maven may fetch its build plugins; consumer network isolation is not claimed.
A direct competing dependency proves that a downloaded literal version remains
a soft Maven request rather than becoming an invented strict constraint. `provided.py` uses the real
existing Nix composition producer through `--input`, preserves its exact hard
POM reference and full projected closure, and compares competing-version
Gradle/Maven behavior with direct consumers of the immutable projection.

Fresh capture and explicit relock add the versioned `nativeMetadata` capability.
For the supported native Java component, a purifier-owned native Maven publication
emits Gradle module metadata without a build identifier. Its native POM writer
adds the metadata marker around the already rendered original POM body;
descriptions, licenses, properties and original callbacks remain intact.
The producer validates the POM dependency graph against the untouched Java
component POM, including types, classifiers, optional edges and exclusions.
Unsupported graphs retain the original JAR/POM for locking, tests and raw release.
Strict projection and CLI publication require complete validated metadata.

With the actual provided input, Gradle rejects an available competing `9.9.9`
through native projected module constraints; Maven retains the projected input
and returns `42`. Downloaded literal versions remain soft requests. Old typed
locks without the capability retain their POM-only scope: the preserved baseline
conflict selected `9.9.9` under Gradle and returned `0`. Explicit relock migrates
the binding; basic replay does not silently upgrade it.

The expected publication is independently built with the exact immutable
`command.nix` packaged in the candidate CLI. Changing the live companion directory after building the CLI changes its
producer derivation identity, so mixed-revision comparisons are invalid. The
separate flake-builder tests and companion byte checks remain required controls.
The immutable companion directory has a canonical store name, and `companion.py`
requires exact command-versus-flake producer derivation identity.

`native.py` checks native default/sentinel/explicit coordinates and snapshot POM
byte parity. `parent.py` checks an unpublished parent and its published child.
`schema.py`, `negative.py`, and `companion.py` check public owner bindings,
collisions, actual task/deployer guards, and immutable companion identity.

The new capability transports the complete accepted projection through native
Upload: module metadata, source classifiers, parent POMs and imported BOM
sidecars retain their bytes. It does not fetch every artifact named by BOM
constraints. Old bindings keep their release-only unclassified JAR/POM limit.
SNAPSHOT routing and custom Upload/deployer behavior remain unsupported.
An arbitrary late Gradle listener can execute user code before a task guard;
atomic deployment is not claimed.

`native-metadata.py` checks owned publication creation, native generator actions,
predicates, late mutations and implicit task dependencies. `metadata-unavailable.py`
proves that an unsupported POM-only edge passes fresh locking, pure tests and raw
release, while both strict Nix projection and CLI publication reject before deployment.

The `legacy-maven` workflow job runs these controls and preserves raw reports,
source identities, SDK paths, consumer definitions and logs on failure as well
as success. No catalog support point follows from a local pass alone.
