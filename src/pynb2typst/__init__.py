from argparse import ArgumentParser
from asyncio.subprocess import PIPE
import os
from io import BytesIO
import base64
from pprint import pp
import sys
from pathlib import Path
import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser, Query, QueryCursor, Tree
from jupyter_client.manager import start_new_kernel
from zmq import ContextTerminated
from IPython.utils.capture import capture_output
from PIL import Image
import subprocess as sp

PY_LANGUAGE = Language(tspython.language())

def get_blocks(tree: Tree) -> [(str, bool)]:
    cursor = tree.walk()
    blocks = []

    cursor.goto_first_child()
    blk_src = bytes('', 'utf-8')

    while True:
        if cursor.node.child(0) and cursor.node.child(0).type == "string":
            blocks.append((blk_src, True))
            blk_src = bytes('', 'utf-8')

            blocks.append((cursor.node.child(0).child(1).text.strip(), False))

        else:
            blk_src += cursor.node.text + bytes('\n', 'utf-8')

        if not cursor.goto_next_sibling():
            break

    blocks.append((blk_src, True))

    blocks = [i for i in blocks if i[0] != bytes('', 'utf-8')]

    return blocks

def execute_code_segments(blocks, connection_file: None | Path = None, kernel_name='py3') -> [tuple]:
    manager, client = start_new_kernel(kernel_name=kernel_name)
    results = []

    for block in blocks:
        results.append(None)
        if not block[1]:
            continue
        client.execute(block[0].decode())
        while True:
            msg = client.get_iopub_msg()

            if msg['msg_type'] == 'execute_input':
                results[-1] = msg['content']
            if msg['msg_type'] == 'execute_result':
                results[-1] = msg['content']
            if msg['msg_type'] == 'display_data':
                msg['content']['execution_count'] = results[-1]['execution_count']
                results[-1] = msg['content']

            if msg['msg_type'] == 'status' and msg['content']['execution_state'] == 'idle':
                break

    return results

def convert_latex_typst(latex: str) -> str:
    proc = sp.Popen(['t2l', 'convert', '-d', 'l2t'], stdin=sp.PIPE, stdout=sp.PIPE)

    result, _ = proc.communicate(latex.replace('\\displaystyle', '').encode())

    return result.decode()


def convert_msg_typst(msg: dict, wd: Path) -> str:
    data = msg['data']

    if 'text/latex' in data:
        return convert_latex_typst(data['text/latex'])
    if 'image/png' in data:
        image = Image.open(BytesIO(base64.b64decode(data['image/png'])))
        image_path = wd.with_name(f'{msg['execution_count']}.png')
        image.save(image_path)
        return f'#figure(\nimage("{image_path.as_posix()}"))'
    if 'text/plain' in data:
        return f'```\n{data['text/plain']}\n```'

def create_typst_file(blocks: [(bytes, bool)], outputs: [dict | None], wd: Path) -> str:

    result = []

    for block, output in zip(blocks, outputs):
        if not block[1]:
            result.append(block[0].decode())
            continue

        if not output:
            continue

        typ = convert_msg_typst(output, wd)
        result.append(typ)

    return "\n".join(result)

def convert_file(filename: Path) -> str:
    parser = Parser(PY_LANGUAGE)
    file_contents = None
    file_tree = None

    try:
        with open(filename, "r") as f:
            file_contents = f.read()
            file_tree = parser.parse(bytes(file_contents, "utf-8"))
    except FileNotFoundError:
        raise Exception("File doesn't exist.")
    except Exception as e:
        raise Exception(f'Error loading file {filename}, {e}')
    
    blocks = get_blocks(file_tree)
    code_segment_outputs = execute_code_segments(blocks)

    result = create_typst_file(blocks, code_segment_outputs, filename)

    return result

def main() -> None:
    parser = ArgumentParser('pynb2typst')
    
    _ = parser.add_argument("filename")
    _ = parser.add_argument("--stdout", action="store_true")
    _ = parser.add_argument("--out", "-o", default="")

    args = parser.parse_args()
    
    file = Path(args.filename)
    output_filename = Path(args.out) if args.out != "" else file.with_suffix(".typ")
    output = convert_file(file)

    if args.stdout:
        sys.stdout.write(output.encode())
        return

    with open(output_filename, "w") as f:
        f.write(output)
