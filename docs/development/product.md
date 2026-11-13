# Product

gradle-purifier makes Nix the dependency backend and the release builder of
Gradle builds: the downloads of a build are locked by URL and hash, its
packages build in the Nix sandbox, and `./gradlew` and the IDE resolve
from the same locked downloads through the store. It meets the contract
of the nix-purifiers (CONTRACT.md of nix-purifiers/purifier). The README
describes what it does for a user.

## What it owns

- The init script (`pkgs/build-gradle-project/purifier.init.gradle`): the redirection of
  every remote repository, the tasks and the project description of the
  lock run, the sandbox build of a project, the replacement of
  publication artifacts in publish mode, and the hint after a build that
  needed a download the lock does not hold.
- The everyday init script (`pkgs/build-gradle-project/user.init.gradle`), which `prefetch`
  installs in the `init.d` of the Gradle user home. It applies the init
  script in interactive mode to the builds of a root that `prefetch`
  prepared.
- The recorder (`src/Recorder.java`): the HTTP server that the lock run
  sends every download through. It serves the version listings that the
  lockfile pins.
- The command (`src/gradle-purifier.sh`, packaged by `pkgs/gradle-purifier`):
  `lock`, `update`, `prefetch` and `publish`.
- The public Nix library (`lib-overlays/default`). The builder
  (`pkgs/build-gradle-project/default.nix`) is a package for `callPackage`.
  The default package overlay adds the scope `gradle-purifier` of `pkgs/`
  to the package set, and the scope holds the builder as
  `buildGradleProject`, beside the command. The builder takes the fetchers and the tree of locked
  downloads from the shared nix-purifiers library. `readLock` is in
  `lib.gradle-purifier`. The command builds through `pkgs/build-gradle-project/command.nix`,
  which calls the same builder, so the command and the flake check a
  lockfile alike.
- The lockfile format (`lib-overlays/default/read-lock.nix`, and the `jq` program of
  `lock` in the command), versioned by its `version` field and sealed by
  its `contentHash`. A file entry is `{ url, hash }` and an inline entry
  `{ path, contents }`; `readLock` derives the path and the mirrors of a
  file entry. `inputsHash` is the hash of the build definition
  (`lib-overlays/default/inputs.nix`), which the command and the stale check compute
  with the same file.

## Principles

- **A lock is complete by construction.** It is what a fresh-cache run of
  every package build, with checks, downloaded through the recorder,
  rather than a list of what Gradle is known to fetch.
- **A lock keeps its resolution.** A relock serves the version listings
  of the lockfile, so a dynamic version resolves as before; only
  `update` moves it.
- **The init script reaches every build.** Gradle applies it to buildSrc
  and to included builds as well as to the root build, so every
  repository of the composite goes through the recorder.
- **One copy of the logic.** The build logic lives in the Nix library;
  the lock run writes only the lockfile.
- **One path per URL.** A download of `https://host/path` is at
  `https/host/path` in the tree of locked downloads, so the lock run, the
  sandbox and the development loop resolve through the same URLs.

## Not in scope

A repository that needs credentials works in the Nix build only through
a fetcher the caller supplies. Kotlin/JS targets are not supported (see
the README for why).
