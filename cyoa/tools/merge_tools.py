from collections import OrderedDict
import copy
from itertools import chain
import operator
import textwrap
from difflib import SequenceMatcher
import json
from pathlib import Path
from hashlib import sha1

from rich.text import Text
from rich.table import Table

from cyoa.tools.lib import *


def obj_hash(value):
    value_ser = json.dumps(value, sort_keys=True, indent=0)
    return sha1(value_ser.encode('utf-8')).hexdigest()


def default_delete_item(seq_a):
    return []


def default_insert_item(seq_b):
    return seq_b


def default_summary(equal_count, replace_count, delete_count, insert_count):
    console.log(
        f"{equal_count=}, {replace_count=}, {delete_count=}, {insert_count=}")


def diff_sequence(seq_a: list, seq_b: list, update_item,
                  delete_item=default_delete_item,
                  insert_item=default_insert_item,
                  summary=default_summary):
    def hash_objects(seq):
        return [
            (obj["id"], obj_hash(obj))
            for obj in seq
        ]

    seq_a_hash = hash_objects(seq_a)
    seq_b_hash = hash_objects(seq_b)

    seq_match = SequenceMatcher(a=seq_a_hash, b=seq_b_hash)
    equal_count = 0
    replace_count = 0
    delete_count = 0
    insert_count = 0
    seq_out = []
    for tag, a_start, a_end, b_start, b_end in seq_match.get_opcodes():
        if tag == 'equal':
            seq_out.extend(seq_a[a_start:a_end])
            equal_count += 1  # No changes, skip
        elif tag == 'replace' and a_end - a_start == b_end - b_start:
            # Same number of items get updated
            old_rows = seq_a[a_start:a_end]
            new_rows = seq_b[b_start:b_end]
            for old_row, new_row in zip(old_rows, new_rows):
                item_data = update_item(old_row, new_row)
                seq_out.append(item_data)

            replace_count += a_end - a_start
        elif tag == 'replace' and a_end - a_start != b_end - b_start:
            # List shrunk
            old_rows = list(map(operator.itemgetter(0),
                                seq_a_hash[a_start:a_end]))
            new_rows = list(map(operator.itemgetter(0),
                                seq_b_hash[b_start:b_end]))
            updated_ids = set(old_rows) & set(new_rows)

            old_rows_items = {item['id']: item for item in seq_a[a_start:a_end]}
            for old_row in seq_a[a_start:a_end]:
                if old_row['id'] in updated_ids:
                    continue

                del_items = delete_item([old_row])
                seq_out.extend(del_items)
                delete_count += 1 - len(del_items)

            for new_row in seq_b[b_start:b_end]:
                if (row_id := new_row['id']) in updated_ids:
                    item_data = update_item(old_rows_items[row_id], new_row)
                    seq_out.append(item_data)
                    replace_count += 1
                else:
                    ins_items = insert_item([new_row])
                    seq_out.extend(ins_items)
                    insert_count += len(ins_items)
        elif tag == 'delete':
            # Allow the delete function to keep some imtems
            del_res = delete_item(seq_a[a_start:a_end])
            seq_out.extend(del_res)
            delete_count += (a_end - a_start) - len(del_res)
        elif tag == 'insert':
            ins_res = insert_item(seq_b[b_start:b_end])
            seq_out.extend(ins_res)
            insert_count += len(ins_res)

    summary(equal_count, replace_count, delete_count, insert_count)
    return seq_out


IMPORTANT_KEYS = (
    'id', 'title', 'titleText', 'text', 'scores',
)

SPECIAL_KEYS = (
    *IMPORTANT_KEYS,
    'addons', 'image', 'requireds'
)

# Theses keys shouldn't be modified
IGNORE_KEYS = ('currentChoices', 'isActive', 'isEditModeOn')

SPECIAL_DISPLAY = {
    'scores': lambda scores: Text.assemble(*intercalate("\n", [
        Text.assemble(score['beforeText'], " ", score['value'], " ", score['afterText'],
                      " (", score['id'], ")", " (cond)" if len(score['requireds']) > 0 else "")
        for score in scores
    ])),
    'requireds': lambda items: Text.assemble(*intercalate("\n", [
        Text.assemble(
            item['beforeText'], " ",
            item['reqId'] if item['type'] == 'id' else
            str.join(", ", [n['req'] for n in item['orRequired']]) if item['type'] == 'or' else
            "Other",
            " ", item['afterText']
        )
        for item in items
    ]))
}


def intercalate(sep, items):
    for idx, item in enumerate(items):
        if idx > 0:
            yield sep
        yield item


def update_dict(old_data: dict, new_data: dict):
    merged_keys = set(old_data.keys() | new_data.keys())
    rest_keys = list(sorted(merged_keys - set(SPECIAL_KEYS)))

    def show_value(value, key=None):
        if key in SPECIAL_DISPLAY:
            return SPECIAL_DISPLAY[key](value)
        elif isinstance(value, str) and len(value) == 0:
            return Text('N/A', style="grey50")
        elif isinstance(value, str):
            return Text(value[:60] + "..." if len(value) > 60 else value)
        elif isinstance(value, list) and len(value) == 0:
            return Text("[]")
        elif isinstance(value, list):
            return Text.assemble(*intercalate("\n", [
                show_value(val) for val in value
            ]))
        elif isinstance(value, dict):
            return Text(json.dumps(value, sort_keys=True, indent=2))
        else:
            return Text(str(value))

    result_dict = {}
    result_changed = False
    diff_table = Table(show_header=False, show_lines=False)
    for key in chain(SPECIAL_KEYS, rest_keys):
        if key in IGNORE_KEYS:
            # Don't change an ignored key
            result_dict[key] = old_data[key] 
        elif key in old_data and key in new_data and old_data[key] != new_data[key]:
            diff_table.add_row(key,
                               show_value(old_data[key], key),
                               show_value(new_data[key], key))
            result_dict[key] = new_data[key]
            result_changed |= True
        elif key in old_data and key not in new_data:
            diff_table.add_row(key,
                               show_value(old_data[key], key),
                               Text("N/A", style="grey50"))
            result_changed |= True
        elif key not in old_data and key in new_data:
            diff_table.add_row(key,
                               Text("N/A", style='grey50'),
                               show_value(new_data[key], key))
            result_dict[key] = new_data[key]
            result_changed |= True
        elif key in IMPORTANT_KEYS and key in old_data:
            diff_table.add_row(key,
                               show_value(old_data[key], key),
                               Text("==", style="grey50"))
            result_dict[key] = old_data[key]
        elif key in old_data:
            result_dict[key] = old_data[key]

    console.log(diff_table)
    if result_changed:
        return result_dict
    else:
        return old_data


class ProjectMergeTool(ToolBase, ProjectUtilsMixin):
    name = 'merge'

    @classmethod
    def setup_parser(cls, parent):
        parser = parent.add_parser(cls.name, help='Format a project file')
        parser.add_argument('--project', dest='project_file',
                            type=Path, required=True)
        parser.add_argument('--patch', dest='patch',
                            type=Path, required=True)
        parser.add_argument('--write', dest='write',
                            action='store_true')
        
        parser.add_argument('--skip-rows', dest='skip_rows',
                            nargs='+', action='extend',
                            default=[])
        parser.add_argument('--skip-objs', dest='skip_objs',
                            nargs='+', action='extend',
                            default=[])
        
        parser.add_argument('--only-rows', dest='only_rows',
                            nargs='+', action='extend',
                            default=[])
        parser.add_argument('--only-objs', dest='only_objs',
                            nargs='+', action='extend',
                            default=[])

    def run(self, args):
        self._load_project(args.project_file)
        patch_project = self._load_file(args.patch)

        def update_object(old_obj, new_obj):
            console.log(f"  Updated Item ({old_obj['id']}): {old_obj['title']}",
                        style="orange1")
            if old_obj['id'] in args.skip_objs:
                console.log(f"    Skipped (in exclusion list)", style="dark_slate_gray1 italic")
                return old_obj
            
            if len(args.only_rows) > 0 and old_obj['id'] not in args.only_objs:
                console.log(f"    Skipped (not in inclusion list)", style="dark_slate_gray1 italic")
                return old_obj
            
            return update_dict(old_obj, new_obj)

        def delete_object(items):
            excluded_rows = []
            for item in items:
                console.log(f"  Deleted Item ({item['id']}): {item['title']}", style="red")
                if item['id'] in args.skip_objs:
                    console.log(f"    Skipped (in exclusion list)", style="dark_slate_gray1 italic")
                    excluded_rows.append(item)
                    continue
                
                if len(args.only_objs) > 0 and item['id'] not in args.only_objs:
                    console.log(f"    Skipped (not in inclusion list)", style="dark_slate_gray1 italic")
                    excluded_rows.append(item)
                    continue

                update_dict(item, item)

            return excluded_rows

        def insert_object(new_items):
            included_rows = []
            for item in new_items:
                console.log(f"  Inserted Item ({item['id']}): {item['title']}", style="green")
                if item['id'] in args.skip_objs:
                    console.log(f"  Skipped (in exclusion list)", style="dark_slate_gray1 italic")
                    continue
                
                if len(args.only_objs) > 0 and item['id'] not in args.only_objs:
                    console.log(f"  Skipped (not in inclusion list)", style="dark_slate_gray1 italic")
                    continue

                update_dict(item, item)
                included_rows.append(item)
                
            return included_rows

        def objects_summary(equal_count, replace_count, delete_count, insert_count):
            if replace_count > 0:
                console.log(f"  Total Updated Objects: {replace_count}")
            if delete_count > 0:
                console.log(f"  Total Deleted Objects: {delete_count}")
            if insert_count > 0:
                console.log(f"  Total Inserted Objects: {insert_count}")

        def update_row(old_row, new_row):
            console.log(f"Updated Row ({old_row['id']}): {old_row['title']}", style="orange1")
            if old_row['id'] in args.skip_rows:
                console.log(f"  Skipped (in exclusion list)", style="dark_slate_gray1 italic")
                return old_row
            
            if len(args.only_rows) > 0 and old_row['id'] not in args.only_rows:
                console.log(f"  Skipped (not in inclusion list)", style="dark_slate_gray1 italic")
                return old_row
            
            old_objects = old_row.pop("objects", [])
            new_objects = new_row.pop("objects", [])

            # Handle updated properties
            if obj_hash(old_row) != obj_hash(new_row):
                console.log("  Updated Row Data", style="orange1")
                updated_row = update_dict(old_row, new_row)
            else:
                updated_row = old_row

            # Handle updated objects
            if obj_hash(old_objects) != obj_hash(new_objects):
                updated_objects = diff_sequence(
                    old_objects,
                    new_objects,
                    update_item=update_object,
                    delete_item=delete_object,
                    insert_item=insert_object,
                    summary=objects_summary
                )
                updated_row["objects"] = updated_objects
            else:
                updated_row["objects"] = old_objects

            return updated_row

        def delete_row(rows):
            excluded_rows = []
            for row in rows:
                console.log(f"Deleted Row ({row['id']}): {row['title']}", style="red")
                if row['id'] in args.skip_rows:
                    console.log(f"  Skipped (in exclusion list)", style="dark_slate_gray1 italic")
                    excluded_rows.append(row)
                    continue
                
                if len(args.only_rows) > 0 and row['id'] not in args.only_rows:
                    console.log(f"  Skipped (not in inclusion list)", style="dark_slate_gray1 italic")
                    excluded_rows.append(row)
                    continue
                
                update_dict(row, row)

            return excluded_rows

        def insert_row(new_rows):
            included_rows = []
            for row in new_rows:
                console.log(f"Inserted Row ({row['id']}): {row['title']}", style="green")
                if row['id'] in args.skip_rows:
                    console.log(f"  Skipped (in exclusion list)", style="dark_slate_gray1 italic")
                    continue
                
                if len(args.only_rows) > 0 and row['id'] not in args.only_rows:
                    console.log(f"  Skipped (not in inclusion list)", style="dark_slate_gray1 italic")
                    continue

                update_dict(row, row)
                included_rows.append(row)

            return included_rows

        def rows_summary(equal_count, replace_count, delete_count, insert_count):
            if replace_count > 0:
                console.log(f"Total Updated Rows: {replace_count}")
            if delete_count > 0:
                console.log(f"Total Deleted Rows: {delete_count}")
            if insert_count > 0:
                console.log(f"Total Inserted Rows: {insert_count}")

        new_rows = diff_sequence(
            self.project["rows"],
            patch_project["rows"],
            update_item=update_row,
            delete_item=delete_row,
            insert_item=insert_row,
            summary=rows_summary
        )
        self.project["rows"] = new_rows

        if args.write:
            self._save_project(args.project_file)


TOOLS = (
    ProjectMergeTool,
)
