PROTOCOL_VERSION = 1
# A sanitised dynamic broker catalogue and its UI projection can exceed the
# legacy 64 KiB shortlist. IPC remains explicitly bounded to one MiB.
MAX_FRAME_SIZE = 1024 * 1024
