def register_cli(add_command):
    add_command("mcp", "serve this board to MCP clients over stdio", _setup, _handle)


def _setup(parser):
    parser.add_argument(
        "--workspace",
        default=None,
        help="workspace or .board folder to serve; MCP clients start servers in their own working folder",
    )
    return parser


def _handle(ctx, args):
    from ..core import find_board_dir
    from .server import run

    path = args.workspace or args.board
    board_path = find_board_dir(path) if path else None
    return {"_exit": run(board_path=board_path)}, ""


def register_tokens(register):
    register("mcp tool schemas", _schema_tokens)


def _schema_tokens(board):
    import json

    from ..core.tokens import estimate
    from .tools import TOOLS

    text = json.dumps(TOOLS, separators=(",", ":"))
    return {"tokens": estimate(text), "detail": f"{len(TOOLS)} tools, {len(text)} chars (only when MCP is enabled)"}
