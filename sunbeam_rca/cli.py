"""CLI entry point for the Sunbeam RCA system."""

from __future__ import annotations

import argparse
import logging
import sys


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="sunbeam-rca",
        description="Root-Cause-Analysis for Sunbeam CI build failures.",
    )
    sub = parser.add_subparsers(dest="command")

    analyze = sub.add_parser("analyze", help="Run RCA analysis")
    analyze.add_argument(
        "--pipeline",
        required=False,
        help="Path to GitHub Actions log archive (.zip)",
    )
    analyze.add_argument(
        "--sosreport",
        required=False,
        help="Path to sosreport tarball (.tar.xz) or extracted directory",
    )
    analyze.add_argument(
        "--output-dir",
        default="./output",
        help="Directory for output reports (default: ./output)",
    )
    analyze.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    serve = sub.add_parser("serve", help="Start the web UI server")
    serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (default: 127.0.0.1)",
    )
    serve.add_argument(
        "--port",
        type=int,
        default=8000,
        help="Bind port (default: 8000)",
    )

    args = parser.parse_args(argv)

    if args.command == "serve":
        _run_server(host=args.host, port=args.port)
        return

    if args.command != "analyze":
        parser.print_help()
        sys.exit(1)

    if not args.pipeline and not args.sosreport:
        parser.error("At least one of --pipeline or --sosreport is required")

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(name)-30s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )

    _run_analysis(
        pipeline_path=args.pipeline or "",
        sosreport_path=args.sosreport or "",
        output_dir=args.output_dir,
    )


def _run_analysis(
    pipeline_path: str,
    sosreport_path: str,
    output_dir: str,
) -> None:
    from sunbeam_rca.graph import build_graph

    logger = logging.getLogger("sunbeam_rca.cli")
    logger.info("Starting RCA analysis")
    logger.info("  Pipeline: %s", pipeline_path or "(none)")
    logger.info("  Sosreport: %s", sosreport_path or "(none)")
    logger.info("  Output: %s", output_dir)

    graph = build_graph()

    initial_state = {
        "pipeline_zip_path": pipeline_path,
        "sosreport_path": sosreport_path,
        "output_dir": output_dir,
        "events": [],
    }

    result = graph.invoke(initial_state)

    candidates = result.get("ranked_candidates", [])
    if candidates:
        top = candidates[0]
        logger.info(
            "Top candidate: %s (%s) confidence=%.2f",
            top["pattern_id"],
            top["category"],
            top["confidence"],
        )
    else:
        logger.info("No root-cause candidates identified.")

    logger.info("Reports written to %s/", output_dir)


def _run_server(host: str, port: int) -> None:
    import uvicorn

    from dotenv import load_dotenv

    load_dotenv()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-30s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )
    logger = logging.getLogger("sunbeam_rca.cli")
    logger.info("Starting web UI at http://%s:%d", host, port)
    uvicorn.run("sunbeam_rca.web.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    main()
