"""MCP tool for running BQL queries against the ledger."""

from mcp.schemas.tools import MCPTool, MCPToolDefinition, MCPToolResult

from ledger.ledger import LedgerManager
from ledger.mcp_tools._common import _fail, _ok, ledger_path, with_middleware
from ledger.models import QueryResult
from ledger.queries import run_query


def _format_query_result(result: QueryResult) -> str:
    """Render a query result as a compact TSV table plus a paging note."""
    header = "\t".join(result.columns)
    if not result.rows:
        return f"{header}\n(no rows)" if header else "(no rows)"
    body = "\n".join("\t".join(cell for cell in row) for row in result.rows)
    table = f"{header}\n{body}" if header else body
    if result.truncated:
        next_offset = result.offset + result.returned_rows
        table += (
            f"\n(truncated at {result.returned_rows} rows; "
            f"pass offset={next_offset} for the next page)"
        )
    return table


async def handle_run_query(args: dict) -> MCPToolResult:
    try:
        offset = max(0, int(args.get("offset") or 0))
        result = run_query(LedgerManager(ledger_path()), args["query"], offset=offset)
    except Exception as exc:
        return _fail("run_query", exc)

    payload = result.model_dump()
    if result.error:
        return _ok(result.error, payload, isError=True)
    return _ok(_format_query_result(result), payload)


run_query_description = """Run a Beancount Query Language (BQL) statement against the ledger. Read-only. Returns a table of string cells, capped at 200 rows; if truncated, recall with offset = previous offset + returned_rows.

BQL is SQL-like but not SQL. Default table is postings (one row per posting, joined to its parent transaction). FROM filters whole transactions so subsets still balance; WHERE filters postings. FROM is not a table name.

SELECT [DISTINCT] <cols>
[FROM <txn-filter> [OPEN ON <date>] [CLOSE [ON <date>]] [CLEAR]]
[WHERE <posting-filter>]
[GROUP BY <keys>] [ORDER BY <keys> [ASC|DESC]] [LIMIT n]

Txn-filter columns: date, year, month, day, flag, payee, narration, tags, links, id, type, accounts. has_account('Invest') is true if any posting account matches.
Posting columns (SELECT/WHERE): those plus account, position, balance, number, currency, cost_number, cost_currency, price, weight, other_accounts, posting_flag, description.

Operators: = != < <= > >= AND OR NOT; ~ regexp on strings (account ~ 'Expenses:Food'); IN for sets ('trip' IN tags). Dates as YYYY-MM-DD. NULL = NULL is TRUE (no SQL three-valued logic). Aggregates (sum, count, first, last, min, max) need GROUP BY on every non-aggregate; no HAVING.

Position/inventory: units(x) strips cost; cost(x) is acquisition cost; convert(x, 'USD') market value; value(x) cost-currency market value. sum(position) yields an Inventory.

OPEN ON date — replace history before date with opening balances (start inclusive). CLOSE ON date — drop entries on/after date (end exclusive). CLEAR — move Income/Expenses to Equity (balance sheet). Combine for period statements.

Shortcuts: JOURNAL "<account-regexp>" [AT COST|UNITS] [FROM …]; BALANCES [AT COST|UNITS] [FROM …]; PRINT [FROM …] (Beancount text).

Examples:
  SELECT account, units(sum(position)), cost(sum(position)) GROUP BY 1 ORDER BY 1
  SELECT date, payee, account, position, balance WHERE account ~ 'Assets:Bank' ORDER BY date
  SELECT account, convert(sum(position), 'USD') WHERE account ~ 'Expenses' GROUP BY 1
  SELECT account, sum(position) FROM OPEN ON 2024-01-01 CLOSE ON 2025-01-01 WHERE account ~ 'Income|Expenses' GROUP BY 1
  SELECT account, sum(position) FROM OPEN ON 2024-01-01 CLOSE ON 2025-01-01 CLEAR WHERE not account ~ 'Income|Expenses' GROUP BY 1
  SELECT date, narration WHERE year = 2024 AND 'trip-ny' IN tags AND account ~ 'Expenses'
  JOURNAL "Assets:Bank" FROM year = 2024
  BALANCES AT COST FROM CLOSE ON 2025-01-01"""


QUERY_TOOLS: tuple[MCPTool, ...] = (
    MCPTool(
        func=with_middleware(handle_run_query),
        definition=MCPToolDefinition(
            name="run_query",
            description=run_query_description,
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "BQL statement (SELECT, JOURNAL, BALANCES, or PRINT). See tool description for syntax and examples.",
                    },
                    "offset": {
                        "type": "integer",
                        "minimum": 0,
                        "default": 0,
                        "description": "Skip this many result rows. When a previous result had truncated=true, pass that offset + returned_rows.",
                    },
                },
                "required": ["query"],
            },
        ),
    ),
)
