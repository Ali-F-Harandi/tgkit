"""Commands package — CLI command implementations.

Each module exports cmd_* functions with signature:
    async def cmd_xxx(args: argparse.Namespace, config: Config) -> int

Returns process exit code (0 = success).
"""
