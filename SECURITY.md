# Security reporting

Do not post molecular structures, credentials, private download links, or an
exploit containing private data in a public issue. Use the repository's private
security advisory reporting when enabled. Otherwise open an issue containing
only a request for a private reporting channel.

The public converter treats PSE as executable input and isolates it from the
service and renderer. A report should identify the release version, affected
compute path, and a minimal public or synthetic reproducer. Avoid testing on
another user's jobs or consuming unbounded shared GPU resources.

Supported security updates target the latest public beta. Deployment operators
must retain their own OAuth keys, restrict access to service state, preserve
the single database writer, and keep their host/Apptainer security updates current.
