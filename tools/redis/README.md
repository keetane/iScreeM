# Redis runtime directory

Redis executables and libraries in this directory are excluded from Git and
are not distributed by the iScreeM source repository.

Redis licensing depends on the exact server version:

- Redis 7.2 and earlier: BSD-3-Clause
- Redis 7.4: RSALv2 or SSPLv1
- Redis 8 and later: AGPL-3.0, RSALv2, or SSPLv1

See the authoritative version matrix at https://redis.io/legal/licenses/.
The tracked `LICENSE.txt` is retained as a convenience copy for the locally
installed Redis 8 runtime; it does not apply to other Redis versions or to
iScreeM itself.
