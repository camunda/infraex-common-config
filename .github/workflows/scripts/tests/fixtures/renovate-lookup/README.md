Fixtures for `check_renovate_lookup_test.sh`.

They live here, as data, rather than in heredocs inside the test: the preset reads
`.sh` and `.yml`, so a fixture written inline would be an annotation of *this*
repository. Renovate would extract it, the checker would report it, and the test
suite would fail the repository it is testing. The `.fixture` suffix keeps them out
of `managerFilePatterns`; the test copies each one to the name the scenario needs.

Only the annotation lines matter here: the checker reads the dependencies from a
Renovate report, and comes back to these files solely to turn an annotation into a
line number.
