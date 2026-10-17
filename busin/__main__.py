"""python -m busin <command>."""
import argparse
import sys

from busin.paths import BUILD


def main(argv=None):
    parser = argparse.ArgumentParser(prog="busin")
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("build", help="build the translated disc image")
    unpack = commands.add_parser("unpack", help="unpack whole images from an images json into build/")
    unpack.add_argument("manifest")
    commands.add_parser("export-pictures", help="export data/picture originals; add .zh.png only when missing")
    commands.add_parser("dump-text", help="write dialogue and small-font text to build/text/")
    init = commands.add_parser("init-text", help="write data/text/<chunk>.json with text left null")
    init.add_argument("chunk", type=int)
    init.add_argument("--align", choices=["left", "center"])
    apply = commands.add_parser("apply-text", help="merge translation fragments into a chunk")
    apply.add_argument("chunk", type=int)
    apply.add_argument("files", nargs="+")
    check = commands.add_parser("check-text", help="check translated lines and print dialog budgets")
    check.add_argument("chunk", type=int)
    check.add_argument("offsets", nargs="*")
    copy = commands.add_parser("copy-text", help="copy translations onto chunks with the same source")
    copy.add_argument("source", type=int)
    copy.add_argument("targets", type=int, nargs="+")
    commands.add_parser("name-entry", help="survey name-entry text and textures into build/")
    commands.add_parser("ui-glyphs", help="render 16x16 UI glyphs with Noto Sans SC Medium")
    disasm = commands.add_parser("disasm", help="disassemble an ELF address range")
    disasm.add_argument("start")
    disasm.add_argument("end")
    disasm.add_argument("--image", action="store_true", help="read build/image/busin-zh.bin")
    extract = commands.add_parser("extract", help="copy one ISO file into build/iso/")
    extract.add_argument("resource")
    state = commands.add_parser("p2s", help="extract a savestate into build/p2s/")
    state.add_argument("state")
    args = parser.parse_args(argv)

    if args.command == "build":
        from busin.image import main as build_main
        return build_main()
    if args.command == "unpack":
        from busin.ui import unpack_manifest
        from pathlib import Path
        unpack_manifest(Path(args.manifest))
        return 0
    if args.command == "export-pictures":
        from busin.ui import export_manifest
        export_manifest()
        return 0
    if args.command == "dump-text":
        from busin.text import dump_dialogue
        from busin.ui_text import dump_manuscript, dump_ui
        print(dump_dialogue())
        print(dump_ui())
        print(dump_manuscript())
        return 0
    if args.command in ("init-text", "apply-text", "check-text", "copy-text"):
        from busin.text_edit import apply_text, check_text, copy_text, init_text
        try:
            if args.command == "init-text":
                path, count, skipped = init_text(args.chunk, args.align)
                print(path, count, "messages; unreadable segments:", " ".join(skipped) or "none")
            elif args.command == "apply-text":
                apply_text(args.chunk, args.files)
            elif args.command == "check-text":
                reports = check_text(args.chunk, args.offsets)
                if reports:
                    print("\n".join(reports))
            else:
                copy_text(args.source, args.targets)
        except ValueError as error:
            raise SystemExit(str(error)) from None
        return 0
    if args.command == "name-entry":
        from busin.naming import survey_name_entry
        print(survey_name_entry())
        return 0
    if args.command == "ui-glyphs":
        from busin.ui_font import render_ui_glyphs
        print(render_ui_glyphs())
        return 0
    if args.command == "disasm":
        from busin.disasm import write_disassembly
        image = BUILD / "image" / "busin-zh.bin" if args.image else None
        path = write_disassembly(int(args.start, 16), int(args.end, 16), image)
        print(path)
        return 0
    if args.command == "extract":
        from busin.disc import extract
        print(extract(args.resource))
        return 0
    if args.command == "p2s":
        from busin.evidence import extract_state
        print(extract_state(args.state))
        return 0
    return 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
