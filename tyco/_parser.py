#!/usr/bin/env python3

import io
import os
import re
import sys
import json
import types
import string
import decimal
import pathlib
import datetime
import importlib
import collections
import collections.abc
from typing import TextIO, Union


__all__ = ['Struct', 'TycoException', 'TycoParseError',
           'load', 'loads', 'load_from_json', 'loads_from_json']


ASCII_CTRL = frozenset(chr(i) for i in range(32)) | frozenset(chr(127))
ILLEGAL_STR_CHARS           = ASCII_CTRL - frozenset("\t")
ILLEGAL_STR_CHARS_MULTILINE = ASCII_CTRL - frozenset("\r\n\t")
BASIC_STR_ESCAPE_REPLACEMENTS = {
        r"\b": "\u0008",  # backspace
        r"\t": "\u0009",  # tab
        r"\n": "\u000A",  # linefeed
        r"\f": "\u000C",  # form feed
        r"\r": "\u000D",  # carriage return
        r'\"': "\u0022",  # quote
        r"\\": "\u005C",  # backslash
    }
BASIC_STR_ESCAPE_REGEX = rf"(?:{'|'.join(re.escape(k) for k in BASIC_STR_ESCAPE_REPLACEMENTS)})"
UNICODE_REGEX = r'\\u([0-9a-fA-F]{4})|\\U([0-9a-fA-F]{8})'
HEXDIGIT_CHARS = frozenset(string.hexdigits)
EOL_REGEX = r'\s*(?:#.*)?' + re.escape(os.linesep)


class TycoException(Exception):
    """Base exception for all errors raised by the Tyco configuration parser."""


class TycoParseError(TycoException):

    """Exception raised when a Tyco document cannot be parsed successfully."""

    def __init__(self, message: str, fragment: 'SourceString') -> None:
        super().__init__(message)
        self.message = message
        self.fragment = fragment            # SourceString()

    def __str__(self) -> str:
        fragment = self.fragment
        if not isinstance(fragment, SourceString):
            return f'{self.__class__.__name__}: {self.message}'
        lexer = getattr(fragment, 'lexer', None)
        if lexer is None:
            return f'{self.__class__.__name__}: {self.message}'
        path = lexer.path or '<string>'
        row = fragment.row
        col = fragment.col
        if row is None or col is None:
            return f'{self.__class__.__name__}: {self.message}'
        line = str(lexer.source_lines[row-1]).rstrip('\n')
        location = f'File "{path}", line {row}, column {col}:'
        visual_col = 0
        for i, ch in enumerate(line):
            if i >= col - 1:
                break
            if ch == '\t':
                visual_col = (visual_col // 8 + 1) * 8  # tab to next 8-char boundary
            else:
                visual_col += 1
        pointer = ' ' * visual_col + '^'
        return f'{location}\n{line}\n{pointer}\n{self.__class__.__name__}: {self.message}'


class SourceString(str):

    __slots__ = ('lexer', 'row', 'col')

    def __new__(cls, value, lexer, row, col):
        obj = super().__new__(cls, value)
        obj.lexer = lexer
        obj.row = row
        obj.col = col
        return obj

    def _location_for_offset(self, offset):
        length = len(self)
        if offset < 0:
            offset += length
        offset = max(0, min(length, offset))
        prefix = str.__getitem__(self, slice(0, offset))
        row = self.row
        col = self.col
        for ch in prefix:
            if ch == '\n':
                row += 1
                col = 1
            else:
                col += 1
        return row, col

    def _wrap(self, value, offset):
        if isinstance(value, SourceString):
            return value
        row, col = self._location_for_offset(offset)
        return SourceString(value, self.lexer, row, col)

    def __getitem__(self, key):
        result = super().__getitem__(key)
        if isinstance(result, SourceString):
            return result
        if isinstance(key, slice):
            start = key.start or 0
            if start < 0:
                start += len(self)
            return self._wrap(result, start)
        if isinstance(key, int):
            idx = key if key >= 0 else len(self) + key
            return self._wrap(result, idx)
        return result

    def __add__(self, other):
        value = super().__add__(other)
        if isinstance(value, SourceString):
            return value
        return SourceString(value, self.lexer, self.row, self.col)

    def __radd__(self, other):
        value = super().__radd__(other)
        if isinstance(other, SourceString):
            return SourceString(value, other.lexer, other.row, other.col)
        return SourceString(value, self.lexer, self.row, self.col)

    def lstrip(self, chars=None):
        stripped = super().lstrip(chars)
        if stripped == str(self):
            return self
        removed = len(self) - len(stripped)
        row, col = self._location_for_offset(removed)
        return SourceString(stripped, self.lexer, row, col)

    def rstrip(self, chars=None):
        stripped = super().rstrip(chars)
        if stripped == str(self):
            return self
        return SourceString(stripped, self.lexer, self.row, self.col)

    def split(self, sep=None, maxsplit=-1):
        if sep is None:
            parts = super().split(sep, maxsplit)
            return [SourceString(part, self.lexer, self.row, self.col) for part in parts]
        sep_len = len(sep)
        if sep_len == 0:
            raise ValueError('empty separator')
        value = str(self)
        start = 0
        splits = 0
        result = []
        while True:
            if maxsplit != -1 and splits >= maxsplit:
                break
            idx = value.find(sep, start)
            if idx == -1:
                break
            result.append(self._wrap(value[start:idx], start))
            start = idx + sep_len
            splits += 1
        result.append(self._wrap(value[start:], start))
        return result

    def strip(self, chars=None):
        return self.lstrip(chars).rstrip(chars)

    @classmethod
    def join(cls, *subs):
        sub = subs[0]
        return cls(''.join(subs), sub.lexer, sub.row, sub.col)


def sub_escape_sequences(content, basic_string=False):

    def repl(match):
        return BASIC_STR_ESCAPE_REPLACEMENTS[match.group(0)]

    escaped = re.sub(BASIC_STR_ESCAPE_REGEX, repl, content)

    def repl(match):
        hex_str = match.group(1) or match.group(2)
        return chr(int(hex_str, 16))

    escaped = re.sub(UNICODE_REGEX, repl, escaped)
    escaped = re.sub(r'/\s*\r?\n\s*', '', escaped)        # remove escaped newline + trailing whitespace
    return escaped


def strip_comments(line):
    content, *comments = line.split('#', maxsplit=1)
    if comments:
        comment = comments[0].rstrip(os.linesep)
        invalid = set(comment) & ILLEGAL_STR_CHARS
        if invalid:
            raise TycoParseError(f'Invalid characters in comments: {invalid!r}', comment)
    return content.rstrip()


def is_whitespace(content):
    return re.match(r'\s*$', str(content))


def cached_property(func):
    @property
    def wrapper(self):
        cache_name = f'_{func.__name__}_cache'
        if not hasattr(self, cache_name):
            setattr(self, cache_name, func(self))
        return getattr(self, cache_name)
    return wrapper


class TycoLexer:

    ire = r'((?!\d)\w+)'            # regex to match identifiers / type names
    attr_ire = r'((?!\d)[\w\.]+)'   # attribute names may include dots
    GLOBAL_SCHEMA_REGEX = rf'([?])?{ire}(\[\])?\s+{attr_ire}\s*:'
    STRUCT_BLOCK_REGEX  = rf'^{ire}:'
    STRUCT_SCHEMA_REGEX = rf'^\s+([*?])?{ire}(\[\])?\s+{attr_ire}\s*:'
    STRUCT_DEFAULTS_REGEX = rf'\s+{attr_ire}\s*:'
    STRUCT_INSTANCE_REGEX = r'\s+-'

    @classmethod
    def from_path(cls, context, path):
        if path not in context._path_cache:
            if not os.path.exists(path):
                raise TycoException(f'Unable to find path {path}')
            if not os.path.isfile(path):
                raise TycoException(f'Can only load path if it is a regular file: {path}')
            base_filename = path
            if base_filename.endswith('.tyco'):
                base_filename = base_filename[:-5]
            module_path = f'{base_filename}.py'
            if os.path.exists(module_path):
                module_name = os.path.basename(base_filename).replace('-', '_')
                spec = importlib.util.spec_from_file_location(module_name, module_path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                try:
                    spec.loader.exec_module(module)
                except Exception:
                    pass
            with open(path) as f:
                lines = list(f.readlines())
            lexer = cls(context, lines, path)
            context._path_cache[path] = lexer
            lexer.process()
        return context._path_cache[path]

    @classmethod
    def from_text_io_wrapper(cls, context, f):
        lines = list(f.readlines())
        path = getattr(f, 'name', '<string>')
        lexer = cls(context, lines, path)
        context._path_cache[id(lexer)] = lexer
        lexer.process()
        return lexer

    @classmethod
    def from_string(cls, context, content):
        lines = content.splitlines(keepends=True)
        lexer = cls(context, lines, path='<string>')
        context._path_cache[id(lexer)] = lexer
        lexer.process()
        return lexer

    def __init__(self, context, source_lines, path):
        self.context = context
        self.source_lines = [SourceString(l, self, i, 1) for i, l in enumerate(source_lines, start=1)]
        self.path = path
        self.lines = collections.deque(self.source_lines)       # what we use to do the work
        self.defaults = {}          # {type_name : {attr_name : TycoInstance|TycoValue|TycoEnum|TycoArray|TycoReference}}
        self.previous_defaults = {} # {type_name : {attr_name : [TycoInstance|TycoValue|TycoEnum|TycoArray|TycoReference]}}

    def process(self):
        while self.lines:
            line = self.lines.popleft()
            if match := re.match(r'#include\s+(\S.*)$', line):
                path = match.groups()[0]
                if not os.path.isabs(path):
                    if self.path is None:
                        rel_dir = os.getcwd()
                    else:
                        rel_dir = os.path.dirname(self.path)
                    path = os.path.join(rel_dir, path)
                lexer = self.__class__.from_path(self.context, path)
                for type_name, attr_defaults in lexer.defaults.items():
                    if type_name not in self.defaults:
                        self.defaults[type_name] = {}
                    type_defaults = self.defaults[type_name]
                    for attr_name, attr in attr_defaults.items():
                        if attr_name in type_defaults:
                            prev_default = type_defaults.pop(attr_name)
                            self.previous_defaults.setdefault(type_name, {}).setdefault(attr_name, []).append(prev_default)
                        type_defaults[attr_name] = attr
                continue
            if match := re.match(self.GLOBAL_SCHEMA_REGEX, line):
                self._load_global(line, match)
                continue
            elif match := re.match(self.STRUCT_BLOCK_REGEX, line):
                type_name = match.groups()[0]
                if type_name not in self.context._structs:
                    self.context._structs[type_name] = struct = TycoStruct(self.context, type_name)
                    self._load_schema(struct)
                struct = self.context._structs[type_name]
                self._load_local_defaults_and_instances(struct)
                continue
            elif not strip_comments(line):
                continue
            raise TycoParseError('Malformatted config line - expecting an include, struct block, or global', line)

    def _load_global(self, line, match):
        global_type_name = None            # we use None as the type_name for the global instance
        options, type_name, array_flag, attr_name = match.groups()
        if attr_name in self.context._structs[global_type_name].schema:
            raise TycoParseError(f"Global attribute '{attr_name}' is defined more than once", line)
        is_array = array_flag == '[]'
        is_nullable = options == '?'
        default_text = line.split(':', maxsplit=1)[1].lstrip()
        if not default_text:
            raise TycoParseError('Must provide a value when setting globals', default_text)
        self.lines.appendleft(default_text)
        attr, delim = self._load_tyco_attr()
        attr.attr_name = attr_name
        is_primary = False
        field_info = TycoField(type_name, is_primary, is_nullable, is_array)
        self.context._structs[global_type_name].schema[attr_name] = field_info
        self.context._global_instance.inst_kwargs[attr_name] = attr

    def _load_schema(self, struct):
        self.defaults[struct.type_name] = {}
        while True:
            if not self.lines:
                break
            content = strip_comments(self.lines[0])
            if not content:                 # blank lines or comments
                self.lines.popleft()
                continue
            if not (match := re.match(self.STRUCT_SCHEMA_REGEX, content)):
                if re.match(r'\s+\w+\s+\w+', content):
                    raise TycoParseError('Schema attribute likely missing trailing colon', content)
                break
            line = self.lines.popleft()
            options, type_name, array_flag, attr_name = match.groups()
            if attr_name in struct.schema:
                raise TycoParseError(f'Duplicate attribute {attr_name} found in {struct.type_name}', line)
            is_array = array_flag == '[]'
            is_primary = options == '*'
            if is_primary and is_array:
                raise TycoParseError('Cannot set a primary key on an array', line)
            is_nullable = options == '?'
            field_info = TycoField(type_name, is_primary, is_nullable, is_array)
            struct.schema[attr_name] = field_info
            default_text = line.split(':', maxsplit=1)[1].lstrip()
            default_content = strip_comments(default_text)
            if default_content:
                self.lines.appendleft(default_text)
                attr, delim = self._load_tyco_attr(in_schema=True)
                attr.attr_name = attr_name
                if isinstance(attr, TycoEnum):
                    if type_name not in TycoValue.base_types:
                        raise TycoParseError(f'Can only set enum values on {TycoValue.base_types}', line)
                    attr.type_name = type_name
                    attr.set_parent(struct)
                self.defaults[struct.type_name][attr_name] = attr

    def _load_local_defaults_and_instances(self, struct):
        while True:
            if not self.lines:
                break
            if self.lines[0].startswith('#include '):
                break
            content = strip_comments(self.lines[0])
            if not content:                 # blank lines or comments
                self.lines.popleft()
                continue
            if not self.lines[0][0].isspace():  # start of a new struct
                break
            if match := re.match(self.STRUCT_SCHEMA_REGEX, self.lines[0]):
                raise TycoParseError('Cannot add schema attributes after initial construction', self.lines[0])
            line = self.lines.popleft()
            if match := re.match(self.STRUCT_DEFAULTS_REGEX, line):
                attr_name = match.groups()[0]
                if attr_name not in struct.schema:
                    raise TycoParseError(f'{attr_name} not found in the schema for {struct}', line)
                default_text = line.split(':', maxsplit=1)[1].lstrip()
                type_defaults = self.defaults[struct.type_name]
                if attr_name in type_defaults:
                    prev_default = type_defaults.pop(attr_name)
                    self.previous_defaults.setdefault(struct.type_name, {}).setdefault(attr_name, []).append(prev_default)
                else:
                    prev_default = None
                if strip_comments(default_text):
                    self.lines.appendleft(default_text)
                    attr, delim = self._load_tyco_attr(in_schema=True)
                    attr.attr_name = attr_name
                    self.defaults[struct.type_name][attr_name] = attr
                elif isinstance(prev_default, TycoEnum):
                    raise TycoParseError(f'{attr_name} previously set as an enum for {struct}, can not set it to empty')
            elif match := re.match(self.STRUCT_INSTANCE_REGEX, line):
                self.lines.appendleft(line.split('-', maxsplit=1)[1].lstrip())
                inst_args = []
                while True:
                    if not self.lines:
                        break
                    inst_content = strip_comments(self.lines[0])
                    if not inst_content:
                        self.lines.popleft()
                        break
                    if inst_content == '\\':                # continues line to next line
                        self.lines.popleft()
                        if self.lines:
                            self.lines[0] = self.lines[0].lstrip()
                        continue
                    attr, delim = self._load_tyco_attr(good_delim=(',', os.linesep), pop_empty_lines=False, allow_attr_name=True)
                    inst_args.append(attr)
                instance_fragment = inst_args[0].fragment if inst_args else line
                default_kwargs = self.defaults[struct.type_name]
                inst = struct.create_instance(inst_args, default_kwargs, instance_fragment)
                globals_map = self.context._global_instance.inst_kwargs
                attr_name = struct.type_name
                if attr_name not in globals_map:                                #TODO handle when name collision with global attr
                    attr = TycoArray(self.context, [], instance_fragment)
                    attr.attr_name = attr_name
                    globals_map[attr_name] = attr
                    is_primary = is_nullable = False
                    is_array = True
                    self.context._structs[None].schema[attr_name] = TycoField(struct.type_name, is_primary, is_nullable, is_array)
                globals_map[attr_name].add_element(inst)

    def _load_tyco_attr(self, good_delim=(os.linesep,), bad_delim='', pop_empty_lines=True, allow_attr_name=False, in_schema=False):
        bad_delim = set(bad_delim) | set('()[],') - set(good_delim)
        if match := re.match(rf'{self.attr_ire}\s*:\s*', self.lines[0]):     # times don't match this regex
            if not allow_attr_name:
                error_text = f'Colon : found in content - enclose in quotes to prevent being used as a field name: {match.groups()[0]}'
                raise TycoParseError(error_text, self.lines[0])
            attr_name = match.groups()[0]
            self.lines[0] = self.lines[0][match.span()[1]:]
            attr, delim = self._load_tyco_attr(good_delim, bad_delim, pop_empty_lines, allow_attr_name=False)
            attr.attr_name = attr_name
            return attr, delim
        ch = self.lines[0][:1]
        if ch == '[':                                               # inline array
            attr = self._load_tyco_array()
            delim = self._strip_next_delim(good_delim)
        elif ch == '(' and in_schema:                               # enums
            attr = self._load_tyco_enums()
            delim = self._strip_next_delim(good_delim)
        elif match := re.match(r'(\w+)\(', self.lines[0]):          # inline instance/reference
            invocation_fragment = self.lines[0]
            type_name = match.groups()[0]
            self.lines[0] = self.lines[0][match.span()[1]:]
            inst_args = self._load_list(')', invocation_fragment, allow_attr_name=True)
            if type_name not in self.context._structs or self.context._structs[type_name].primary_keys:
                attr = TycoReference(self.context, inst_args, type_name, invocation_fragment)
            else:
                default_kwargs = self.defaults[type_name]
                attr = self.context._structs[type_name].create_instance(inst_args, default_kwargs, invocation_fragment)
            delim = self._strip_next_delim(good_delim)
        elif ch in ('"', "'"):                                      # quoted string
            opening_fragment = self.lines[0]
            if (triple := ch*3) == self.lines[0][:3]:
                quoted_string = self._load_triple_string(triple, opening_fragment)
            else:
                quoted_string = self._load_single_string(ch, opening_fragment)
            attr = TycoValue(self.context, fragment=quoted_string)
            delim = self._strip_next_delim(good_delim)
        else:
            attr, delim = self._strip_next_attr_and_delim(good_delim, bad_delim)
        self.lines[0] = self.lines[0].lstrip(' \t')                 # do not strip off newlines
        if pop_empty_lines and not self.lines[0]:
            self.lines.popleft()
        return attr, delim

    def _strip_next_delim(self, good_delim):
        delim_regex = '^' + '|'.join(re.escape(d) for d in good_delim)
        if not (match := re.match(delim_regex, self.lines[0])):
            if os.linesep in good_delim and not (content := strip_comments(self.lines[0])):
                delim = os.linesep                      # handles the case where we only have trailing comments
                self.lines[0] = self.lines[0][:0]
                return delim
            raise TycoParseError(f'Unabled to find expected delimiter: {good_delim}', self.lines[0])
        delim = match.group()
        start, end = match.span()
        self.lines[0] = self.lines[0][end:]
        return delim

    def _strip_next_attr_and_delim(self, good_delim, bad_delim):
        all_content = strip_comments(self.lines[0]) + os.linesep
        all_delim = list(good_delim) + list(bad_delim)
        delim_regex = '|'.join(re.escape(d) for d in all_delim)
        if not (match := re.search(delim_regex, all_content)):
            raise TycoParseError(f'Unable to find expected delimiter: {good_delim}', self.lines[0])
        delim = match.group()
        if delim in bad_delim:
            raise TycoParseError(f'Delimiter character {delim!r} found - enclose with quotes if correct', self.lines[0])
        start, end = match.span()
        text = all_content[:start].strip()
        if not text:
            raise TycoParseError('Value not found - use empty string with quotes "" if truly no content', all_content)
        attr = TycoValue(self.context, fragment=text)
        self.lines[0] = self.lines[0][end:]                              # inline comments might be part of
        return attr, delim                                               # a string so dont use all_content

    def _load_tyco_array(self):
        opening_fragment = self.lines[0]
        self.lines[0] = self.lines[0][1:]       # strip off leading [
        content = self._load_list(']', opening_fragment)
        return TycoArray(self.context, content, opening_fragment)

    def _load_tyco_enums(self):
        opening_fragment = self.lines[0]
        self.lines[0] = self.lines[0][1:]       # strip off leading (
        enums = self._load_list(')', opening_fragment)
        return TycoEnum(self.context, enums, opening_fragment)

    def _load_list(self, closing_char, opening_fragment, allow_attr_name=False):
        good_delims = (closing_char, ',')
        bad_delims  = ')' if closing_char == ']' else ']'
        array = []
        while True:
            if not self.lines:
                raise TycoParseError(f"Unterminated list; expected '{closing_char!r}' before end of file", opening_fragment)
            if not strip_comments(self.lines[0]):                       # can have newlines within the array
                self.lines.popleft()
                continue
            if self.lines[0].startswith(closing_char):                  # can happen with a trailing comma
                self.lines[0] = self.lines[0][1:]
                break
            attr, delim = self._load_tyco_attr(good_delims, bad_delims, allow_attr_name=allow_attr_name)
            array.append(attr)
            if delim == closing_char:
                break
        return array

    def _load_triple_string(self, triple, opening_fragment):
        is_literal = triple == "'''"
        start = 3
        all_contents = []
        while True:
            if not self.lines:
                raise TycoParseError(f'Unterminated triple-quoted {triple} string', opening_fragment)
            line = self.lines.popleft()
            end = line.find(triple, start)
            if end != -1:
                end += 3                      # include the triple quote at end
                content = line[:end]
                remainder = line[end:]
                all_contents.append(content)
                break
            else:
                if not is_literal and line.endswith('\\' + os.linesep): # we strip trailing whitespace
                    line = line[:-(1+len(os.linesep))]                  # following a trailing slash
                    while self.lines:
                        self.lines[0] = self.lines[0].lstrip()
                        if not self.lines[0]:
                            self.lines.popleft()
                        else:
                            break
            all_contents.append(line)
            start = 0
        for i in range(2):                  # edge case: there can be a max of 2 additional quotes
            if remainder.startswith(triple[0]):
                all_contents[-1] += triple[0]
                remainder = remainder[1:]
            else:
                break
        final_content = SourceString.join(*all_contents)
        if invalid := set(final_content) & ILLEGAL_STR_CHARS_MULTILINE:
            error_text = f'Literal multiline strings must not contain control characters (found {invalid!r})'
            raise TycoParseError(error_text, final_content[:500])
        self.lines.appendleft(remainder)
        return final_content

    def _load_single_string(self, ch, opening_fragment):
        is_literal = ch == "'"
        start = 1
        line = self.lines.popleft()
        while True:
            end = line.find(ch, start)
            if end == -1:
                raise TycoParseError(f'Unterminated string literal (missing closing quote {ch})', opening_fragment)
            if is_literal or line[end-1] != '\\':       # this is an escaped quote
                break
            start = end + 1
        end += 1                            # include quote at the end
        final_content = line[:end]
        remainder = line[end:]
        if invalid := set(final_content) & ILLEGAL_STR_CHARS:
            error_text = f'Literal strings may not contain control characters (found {invalid!r})'
            raise TycoParseError(error_text, final_content[:500])
        self.lines.appendleft(remainder)
        return final_content


class TycoContext:

    def __init__(self):
        self._path_cache = {}                                 # {path : TycoLexer()}
        self._global_instance = TycoInstance(self, {}, None)  # we put everything into a global instance with type_name None
        self._structs = {None: TycoStruct(self, None)}        # {type_name : TycoStruct}

    def _render_content(self):
        self._set_parents()
        self._validate_field_info()
        self._render_base_content()
        self._load_primary_keys()
        self._render_references()
        self._render_templates()

    def _set_parents(self):
        for lexer in self._path_cache.values():
            for type_name, attrs in lexer.defaults.items():
                struct = self._structs[type_name]
                for attr in attrs.values():
                    attr.set_parent(struct)
        for attr in self._global_instance.inst_kwargs.values():
            attr.set_parent(self._global_instance)

    def _validate_field_info(self):
        for lexer in self._path_cache.values():
            for defaults in (lexer.defaults, lexer.previous_defaults):
                for attrs in defaults.values():
                    for vals in attrs.values():
                        if not isinstance(vals, list):
                            vals = [vals]
                        for val in vals:
                            val.validate_field_info()
        for attr in self._global_instance.inst_kwargs.values():
            attr.validate_field_info()

    def _render_base_content(self):
        for lexer in self._path_cache.values():
            for defaults in (lexer.defaults, lexer.previous_defaults):
                for attrs in defaults.values():
                    for vals in attrs.values():
                        if not isinstance(vals, list):
                            vals = [vals]
                        for val in vals:
                            val.render_base_content()
        for attr in self._global_instance.inst_kwargs.values():
            attr.render_base_content()

    def _load_primary_keys(self):                               # primary keys can only be base types w/o templating
        for attr in self._global_instance.inst_kwargs.values():
            attr.load_primary_keys()

    def _render_references(self):
        for lexer in self._path_cache.values():
            for defaults in (lexer.defaults, lexer.previous_defaults):
                for attrs in defaults.values():                 # we render defaults even if they don't get used
                    for vals in attrs.values():
                        if not isinstance(vals, list):
                            vals = [vals]
                        for val in vals:
                            val.render_references()
        for attr in self._global_instance.inst_kwargs.values():
            attr.render_references()

    def _render_templates(self):
        for attr in self._global_instance.inst_kwargs.values():
            attr.render_templates()

    def as_object(self):
        return Struct(**{a : i.as_object() for a, i in self._global_instance.inst_kwargs.items()})

    def as_json(self):
        return {a : i.as_json() for a, i in self._global_instance.inst_kwargs.items()}

    def dumps_json(self, **json_kwargs):
        """Return a JSON string representation of the context."""
        return json.dumps(self.as_json(), **json_kwargs)

    def dump_json(self, file_or_path: Union[str, os.PathLike, TextIO], **json_kwargs) -> None:
        """Serialize the context to JSON and write it to a path or file-like object."""
        content = self.dumps_json(**json_kwargs)
        if isinstance(file_or_path, (str, os.PathLike)):
            with open(file_or_path, 'w', encoding='utf-8') as fh:
                fh.write(content)
        else:
            file_or_path.write(content)

    def dump(self, file_or_path: Union[str, os.PathLike, TextIO], compact: bool = False) -> None:
        """Serialize the context back into Tyco and write it to a path or file-like object."""
        content = self.dumps(compact=compact)
        if isinstance(file_or_path, (str, os.PathLike)):
            with open(file_or_path, 'w', encoding='utf-8') as fh:
                fh.write(content)
        else:
            file_or_path.write(content)

    def dumps(self, compact: bool = False) -> str:
        """Serialize the context back into Tyco and return it as a string."""
        global_attributes = {}
        struct_attributes = collections.OrderedDict()
        for attr_name, struct in self._structs.items():
            if attr_name is not None and attr_name not in self._global_instance.inst_kwargs:
                struct_attributes[attr_name] = None                 # put the inline-only structs first
        for attr_name, attr in self._global_instance.inst_kwargs.items():
            if attr_name in self._structs:
                struct_attributes[attr_name] = attr
            else:
                global_attributes[attr_name] = attr
        output = []     # lines of tyco content
        # write the global attributes to the top
        dump_schema = self._create_dump_schema()
        show_attr = not compact
        for attr_name, attr in global_attributes.items():
            output.extend(f'{attr.type_name} {attr.dump_tyco(dump_schema, show_attr=True)}\n')
        for i, (type_name, tyco_array) in enumerate(struct_attributes.items()):
            if i > 0 or global_attributes:
                output.append('\n')
            output.append(f'{type_name}:\n')
            schema, defaults = dump_schema[type_name]
            for attr_name, field_info in schema.items():
                options = ' '
                if field_info.is_primary:
                    options = '*'
                elif field_info.is_nullable:
                    options = '?'
                array_flag = '[]' if field_info.is_array else ''
                line = f' {options}{field_info.type_name}{array_flag} {attr_name}:'
                if attr_name in defaults:
                    line += f' {defaults[attr_name].dump_tyco(dump_schema, show_attr=False)}\n'
                else:
                    line += '\n'
                output.append(line)
            if tyco_array is not None:          # is set to None above for inline-only structs
                output.extend(tyco_array.dump_tyco(dump_schema, show_attr, base_format=True))
        return ''.join(output)

    def _create_dump_schema(self):
        # order of schema goes from most diverse to most common elements
        dump_schema = {}        # {type_name : (schema, defaults)}
        for type_name, struct in self._structs.items():
            if type_name is None:           # we handle global in the original dump
                continue
            most_common = []
            for attr_name, field_info in struct.schema.items():
                values = collections.Counter(i[attr_name] for i in struct.instances)
                val, count =  values.most_common(1)[0]
                most_common.append((count, attr_name, val, field_info))
            schema, defaults = collections.OrderedDict(), {}
            most_common.sort()      # puts them in least common order
            for count, attr_name, val, field_info in most_common:
                schema[attr_name] = field_info
                if count > 1:
                    defaults[attr_name] = val
            dump_schema[type_name] = (schema, defaults)
        return dump_schema


TycoField = collections.namedtuple('TycoField', 'type_name is_primary is_nullable is_array')


class TycoStruct:

    def __init__(self, context, type_name, schema=None):
        self.context = context
        self.type_name = type_name
        self.schema = schema or collections.OrderedDict()   # {attr_name : TycoField}
        self.instances = []                                 # used when dumping
        self.mapped_instances = {}                          # {primary_keys : TycoInstance}

    @cached_property
    def attr_names(self):
        return list(self.schema)

    @cached_property
    def primary_keys(self):
        return list(a for a, s in self.schema.items() if s.is_primary)

    def create_instance(self, inst_args, default_kwargs, fragment):
        local_kwargs = {}
        kwargs_only = False
        for i, attr in enumerate(inst_args):
            if not attr.attr_name:
                if kwargs_only:
                    raise TycoParseError(f"Positional arguments for '{self.type_name}' must appear before keyed arguments", attr.fragment)
                attr.attr_name = self.attr_names[i]
            else:
                kwargs_only = True
            local_kwargs[attr.attr_name] = attr
            attr.set_parent(self)
        inst_fragment = fragment
        if inst_fragment is None and inst_args:
            inst_fragment = getattr(inst_args[0], 'fragment', None)
        if inst_fragment is None:
            raise TycoException(f'Internal parser error: fragment missing for struct {self.type_name}')
        inst_kwargs = {}
        for attr_name in self.schema:
            if isinstance(default_kwargs.get(attr_name), TycoEnum):
                enums = default_kwargs[attr_name]
                if attr_name not in local_kwargs:
                    raise TycoParseError(f"{attr_name} enum value not set for struct '{self.type_name}'", inst_fragment)
                attr = local_kwargs[attr_name]
                if not enums.is_valid(attr):
                    raise TycoParseError(f"{attr_name} enum value {attr} for struct '{self.type_name}' not in choices: {enums}", inst_fragment)
            elif attr_name in local_kwargs:
                attr = local_kwargs[attr_name]
            elif attr_name in default_kwargs:
                attr = default_kwargs[attr_name].make_copy()
            else:
                raise TycoParseError(f"Invalid attribute {attr_name} for struct '{self.type_name}': "
                                     f"value is required and no default is defined", inst_fragment)
            attr.attr_name = attr_name
            inst_kwargs[attr_name] = attr
        inst = TycoInstance(self.context, inst_kwargs, self.type_name, inst_fragment)
        self.instances.append(inst)
        return inst

    def load_primary_keys(self, inst):
        if not self.primary_keys:
            return
        key = tuple(inst[k].as_object() for k in self.primary_keys)
        if key in self.mapped_instances:
            raise TycoParseError(f"{inst.type_name} with primary key {key} already exists", inst.fragment)
        self.mapped_instances[key] = inst

    def load_reference(self, inst_args):
        inst_kwargs = {}
        kwargs_only = False
        for i, attr in enumerate(inst_args):
            if not attr.attr_name:
                if kwargs_only:
                    raise TycoParseError(f"Positional reference arguments for '{self.type_name}' must appear before keyed arguments", attr.fragment)
                attr.attr_name = self.primary_keys[i]
                kwargs_only = True
            attr.set_parent(self)                   # use a struct instance as the parent since it has the schema
            attr.render_base_content()
            inst_kwargs[attr.attr_name] = attr
        ordered_attrs = tuple(inst_kwargs[attr_name] for attr_name in self.primary_keys)
        key = tuple(a.as_object() for a in ordered_attrs)
        if key not in self.mapped_instances:
            fragment = inst_kwargs[self.primary_keys[0]].fragment
            raise TycoParseError(f"{self.type_name}{key!r} is referenced, but instance can not be found", fragment)
        return ordered_attrs, self.mapped_instances[key]

    def __str__(self):
        return f'TycoStruct({self.type_name})'

    def __repr__(self):
        return self.__str__()


class TycoInstance:

    _unrendered = object()

    def __init__(self, context, inst_kwargs, type_name, fragment=None):
        self.context = context
        self.inst_kwargs = inst_kwargs      # {attr_name : TycoValue|TycoInstance|TycoArray|TycoReference}
        self.type_name = type_name
        self.fragment = fragment
        self.attr_name  = None              # set later
        self.parent     = None              # set later
        self._as_object = self._unrendered
        self._as_json   = self._unrendered

    def make_copy(self):
        inst_kwargs = {}
        for attr_name, attr in self.inst_kwargs.items():
            inst_kwargs[attr_name] = attr.make_copy()
            inst_kwargs[attr_name].attr_name = attr.attr_name
        return self.__class__(self.context, inst_kwargs, self.type_name, self.fragment)

    @property
    def schema(self):
        if self.type_name not in self.context._structs:
            self._error(f"Struct not previously defined: {self}")
        return self.context._structs[self.type_name].schema

    def set_parent(self, parent):
        self.parent = parent                # not used
        for attr in self.inst_kwargs.values():
            attr.set_parent(self)

    @property
    def field_info(self):
        return self.parent.schema[self.attr_name]

    def validate_field_info(self):
        if self.parent is None or self.attr_name is None:
            raise TycoException(f'Internal parser error: {self.parent=} or {self.attr_name=} not set')
        if self.field_info.type_name != self.type_name:
            self._error(f"Instance of {self} used for field {self.attr_name} that expects type {self.field_info.type_name}")
        if self.field_info.is_array and not isinstance(self.parent, TycoArray):
            self._error(f"Instance of '{self}' used for field {self.attr_name} which expects a list")
        for attr_type, attr in self.inst_kwargs.items():
            attr.validate_field_info()

    def render_base_content(self):
        for i in self.inst_kwargs.values():
            i.render_base_content()

    def load_primary_keys(self):
        self.context._structs[self.type_name].load_primary_keys(self)

    def render_references(self):
        for i in self.inst_kwargs.values():
            i.render_references()

    def render_templates(self):
        for i in self.inst_kwargs.values():
            i.render_templates()

    def as_object(self):
        if self._as_object is self._unrendered:
            kwargs = {a : v.as_object() for a, v in self.inst_kwargs.items()}
            self._as_object = _Struct.create_object(self.context, self.type_name, **kwargs)
        return self._as_object

    def as_json(self):
        if self._as_json is self._unrendered:
            self._as_json = {a : v.as_json() for a, v in self.inst_kwargs.items()}
        return self._as_json

    def dump_tyco(self, dump_schema, show_attr, base_format=False):
        dumped = []
        schema, defaults = dump_schema[self.type_name]
        i_show_attr = show_attr
        for attr_name in schema:
            attr = self.inst_kwargs[attr_name]
            if attr_name in defaults and attr == defaults[attr_name]:
                i_show_attr = True               # now we have to prefix attr_name:
            else:
                dumped.append(attr.dump_tyco(dump_schema, i_show_attr))
        if base_format:
            return ', '.join(dumped)
        else:
            dumped = f'{self.type_name}({", ".join(dumped)})'
            if show_attr:
                dumped = f'{self.attr_name}: {dumped}'
            return dumped

    def __getitem__(self, attr_name):
        return self.inst_kwargs[attr_name]

    def __contains__(self, attr_name):
        return attr_name in self.inst_kwargs

    def __eq__(self, other):
        return self.as_object() == other.as_object()

    def __hash__(self):
        return hash(tuple(self.inst_kwargs[a] for a in self.schema))

    def __str__(self):
        return f'TycoInstance({self.type_name}: {self.inst_kwargs})'

    def __repr__(self):
        return self.__str__()

    def _error(self, message):
        raise TycoParseError(message, self.fragment)


class TycoReference:                    # Lazy container class to refer to instances

    _unrendered = object()

    def __init__(self, context, inst_args, type_name, fragment=None):
        self.context = context
        self.inst_args = inst_args          # [TycoValue,...]
        self.type_name = type_name
        self.fragment = fragment
        self.attr_name = None               # set later
        self.parent    = None               # set later
        self._dereference = self._unrendered
        self._ordered_attrs = self._unrendered

    def make_copy(self):
        inst_args = [i.make_copy() for i in self.inst_args]
        return self.__class__(self.context, inst_args, self.type_name, self.fragment)

    @property
    def schema(self):
        if self.type_name not in self.context._structs:
            self._error(f"Struct not previously defined: {self}")
        return self.context._structs[self.type_name].schema

    def set_parent(self, parent):
        self.parent = parent                # not used

    @property
    def field_info(self):
        return self.parent.schema[self.attr_name]

    def validate_field_info(self):
        if self.parent is None or self.attr_name is None:
            raise TycoException(f'Internal parser error: {self.parent=} or {self.attr_name=} not set')
        if self.field_info.type_name != self.type_name:
            self._error(f"Reference for {self} used for field {self.attr_name} that expects type {self.field_info.type_name}")
        if self.field_info.is_array and not isinstance(self.parent, TycoArray):
            self._error(f"Reference for '{self}' used for field {self.attr_name} which expects a list")

    def render_base_content(self):
        pass

    def load_primary_keys(self):
        pass

    def render_references(self):
        if self._dereference is self._unrendered:
            struct = self.context._structs[self.type_name]
            self._ordered_attrs, self._dereference = struct.load_reference(self.inst_args)

    def render_templates(self):
        pass

    def _error(self, message):
        raise TycoParseError(message, self.fragment)

    def __getitem__(self, attr_name):
        return self._dereference[attr_name]

    def __contains__(self, attr_name):
        return self._dereference.__contains__(attr_name)

    def as_object(self):
        return self._dereference.as_object()

    def as_json(self):
        return self._dereference.as_json()

    def dump_tyco(self, *args, **kwargs):       # dump_schema and show_attr get ignored
        ordered_tyco = f', '.join(a.dump_tyco(*args, **kwargs) for a in self._ordered_attrs)
        return f'{self.type_name}({ordered_tyco})'

    def __eq__(self, other):
        return self.as_object() == other.as_object()

    def __hash__(self):
        return hash(self._dereference)

    def __str__(self):
        return f'TycoReference({self.type_name}: {self.inst_args})'

    def __repr__(self):
        return self.__str__()


class TycoEnum:

    def __init__(self, context, elements, fragment):
        self.context = context
        self.elements = list(elements)
        self.fragment = fragment
        self.attr_name = None
        self.type_name = None
        self.parent = None

    def make_copy(self):
        return self.__class__(self.context, [i.make_copy() for i in self.elements], self.fragment)

    def set_parent(self, parent):
        self.parent = parent
        for element in self.elements:
            element.attr_name = self.attr_name
            element.set_parent(parent)

    def validate_field_info(self):
        for element in self.elements:
            element.validate_field_info()

    def render_base_content(self):
        for element in self.elements:
            element.render_base_content()

    def load_primary_keys(self):
        for element in self.elements:
            element.load_primary_keys()

    def render_references(self):
        for element in self.elements:
            element.render_references()

    def render_templates(self):
        for element in self.elements:
            element.render_templates()

    @cached_property
    def _choice_values(self):
        for element in self.elements:
            element.render_base_content()
        return {element.as_object() for element in self.elements}

    def is_valid(self, attr):
        attr.render_base_content()
        return attr.as_object() in self._choice_values

    def __str__(self):
        return f'TycoEnum({self.attr_name}: {self.elements})'

    def __repr__(self):
        return self.__str__()


class TycoArray:

    _unrendered = object()

    def __init__(self, context, elements, fragment=None):
        self.context = context
        self._elements = elements            # [TycoInstance|TycoValue|TycoReference,...]
        self.fragment = fragment
        self._attr_name = None               # set later
        self.parent     = None               # set later
        self._as_object = self._unrendered
        self._as_json = self._unrendered
        self._set_element_parents()

    def _set_element_parents(self):
        for i in self._elements:
            i.set_parent(self)

    def add_element(self, element):
        self._elements.append(element)
        if self.attr_name is not None:
            element.attr_name = self.attr_name

    def make_copy(self):
        return self.__class__(self.context, [i.make_copy() for i in self._elements], self.fragment)

    @property
    def attr_name(self):
        return self._attr_name

    @attr_name.setter
    def attr_name(self, value):
        self._attr_name = value
        for i in self._elements:
            i.attr_name = value

    @property
    def schema(self):                                   # we return the schema for the parent
        return self.parent.schema                       # and then downstream handle things like is_array

    def set_parent(self, parent):
        self.parent = parent
        for i in self._elements:
            i.set_parent(self)

    @property
    def field_info(self):
        return self.parent.schema[self.attr_name]

    def validate_field_info(self):
        if self.parent is None or self.attr_name is None:
            raise TycoException(f'Internal parser error: {self.parent=} or {self.attr_name=} not set')
        if not self.field_info.is_array:
            self._error(f"The schema for '{self.attr_name}' does not indicate this is an array."
                        f" Append [] to the schema definition if {self.field_info.type_name}.{self.attr_name} should be an array.")
        for i in self._elements:
            i.validate_field_info()

    def render_base_content(self):
        for i in self._elements:
            i.render_base_content()

    def load_primary_keys(self):
        for i in self._elements:
            i.load_primary_keys()

    def render_references(self):
        for i in self._elements:
            i.render_references()

    def render_templates(self):
        for i in self._elements:
            i.render_templates()

    def as_object(self):
        if self._as_object is self._unrendered:
            self._as_object = [i.as_object() for i in self._elements]
        return self._as_object

    def dump_tyco(self, dump_schema, show_attr, base_format=False):
        if base_format:
            dumped = [i.dump_tyco(dump_schema, show_attr, base_format) for i in self._elements]
            dumped = ''.join(f'  - {d}\n' for d in dumped)
        else:
            element_show_attr = False if self.field_info.type_name in TycoValue.base_types else show_attr
            dumped = [i.dump_tyco(dump_schema, element_show_attr) for i in self._elements]
            dumped = f'[{", ".join(dumped)}]'
            if show_attr:
                dumped = f'{self.attr_name}: {dumped}'
        return dumped

    def as_json(self):
        if self._as_json is self._unrendered:
            self._as_json = [i.as_json() for i in self._elements]
        return self._as_json

    @property
    def type_name(self):        # normally set by the schema, but here inferred for the json converter
        if not self._elements:
            return 'null'
        type_names = set(i.type_name for i in self._elements)
        if len(type_names) != 1:
            raise TycoException('Mixed types found in elements')    # this raises then is caught
        return type_names.pop()

    def __eq__(self, other):
        return self.as_object() == other.as_object()

    def __hash__(self):
        return hash(tuple(self._elements))

    def __str__(self):
        try:
            type_name = f'{self.field_info.type_name}: '
        except Exception:
            type_name = ''
        return f'TycoArray({type_name}{self._elements})'

    def __repr__(self):
        return self.__str__()

    def _error(self, message):
        raise TycoParseError(message, self.fragment)


class TycoValue:

    TEMPLATE_REGEX = r'\{([\w\.]+)\}'
    base_types = {'str', 'int', 'bool', 'float', 'decimal', 'date', 'time', 'datetime', 'null'}
    special_characters = set('{}[],():,#') | set(chr(i) for i in (*range(0x20), 0x7F))
    _unrendered = object()

    def __init__(self, context, fragment=None):
        self.context = context
        self.fragment = fragment
        self.attr_name = None           # set later
        self.parent    = None           # set later
        self.is_literal_str = False
        self._rendered = self._unrendered
        self._as_object = self._unrendered
        self._as_json = self._unrendered

    def make_copy(self):
        return self.__class__(self.context, self.fragment)

    def apply_field_info(self, **field_info):
        for attr, val in field_info.items():
            setattr(self.field_info, attr, val)

    def set_parent(self, parent):
        self.parent = parent

    @property
    def field_info(self):
        return self.parent.schema[self.attr_name]

    def validate_field_info(self):
        if self.parent is None or self.attr_name is None:
            raise TycoException(f'Internal parser error: {self.parent=} or {self.attr_name=} not set')
        if self.field_info.is_array is True and not (self.field_info.is_nullable is True and self.fragment == 'null'):
            if not isinstance(self.parent, TycoArray) and self._as_object is not None:
                self._error(f"Schema indicates that this should be an array, but found a single value for '{self.attr_name}'")
        if self.field_info.type_name is not None and self.field_info.type_name not in self.base_types:
            if self._as_object is None:
                return
            self._error(f"Invalid {self.field_info.type_name} type - must be one of: {self.base_types}")

    def render_base_content(self):
        if self._rendered is not self._unrendered:
            return
        text = str(self.fragment)
        if self.field_info.is_nullable and text == 'null':
            rendered = None
        elif self.field_info.type_name == 'str':
            self.is_literal_str = text.startswith("'")
            if text[:3] in ("'''", '"""'):
                text = text[3:-3]
                if text.startswith(os.linesep):                 # strip single leading newline
                    text = text[len(os.linesep):]
            elif text[:1] in ("'", '"'):
                text = text[1:-1]
            rendered = text
        elif self.field_info.type_name == 'int':
            if text.startswith('0x'):
                base = 16
            elif text.startswith('0o'):
                base = 8
            elif text.startswith('0b'):
                base = 2
            else:
                base = 10
            try:
                rendered = int(text, base)
            except ValueError:
                self._error(f"'{text}' is not a valid integer literal")
        elif self.field_info.type_name == 'float':
            try:
                rendered = float(text)
            except ValueError:
                self._error(f"'{text}' is not a valid floating-point literal")
        elif self.field_info.type_name == 'decimal':
            try:
                rendered = decimal.Decimal(text)
            except decimal.InvalidOperation:
                self._error(f"'{text}' is not a valid decimal literal")
        elif self.field_info.type_name == 'bool':
            if text == 'true':
                rendered = True
            elif text == 'false':
                rendered = False
            else:
                self._error(
                    f"Boolean fields must be either 'true' or 'false', but '{text}' was provided"
                )
        elif self.field_info.type_name == 'date':
            try:
                rendered = datetime.date.fromisoformat(text)
            except ValueError:
                self._error(f"'{text}' is not a valid ISO-8601 date (YYYY-MM-DD)")
        elif self.field_info.type_name == 'time':
            try:
                rendered = datetime.time.fromisoformat(text)
            except ValueError:
                self._error(f"'{text}' is not a valid ISO-8601 time (HH:MM:SS)")
        elif self.field_info.type_name == 'datetime':
            try:
                rendered = datetime.datetime.fromisoformat(text)
            except ValueError:
                self._error(
                    f"'{text}' is not a valid ISO-8601 datetime (YYYY-MM-DD HH:MM:SS±TZ)"
                )
        else:
            self._error(f"Unsupported type '{self.field_info.type_name}'")
        self._rendered = rendered

    def load_primary_keys(self):
        pass

    def render_references(self):
        pass

    def render_templates(self):
        if self._as_object is not self._unrendered:
            return
        self._as_object = self._rendered
        if not self.field_info.type_name == 'str' or self.is_literal_str:
            return
        if self.field_info.is_nullable and self._as_object is None:
            return

        def template_render(match):
            obj = self
            template_var = match.groups()[0]
            if not template_var.startswith('.'):    # implied . when not given
                template_var = f'.{template_var}'
            while template_var.startswith('.'):     # .. = parent's parent, ... and so on
                template_var = template_var[1:]
                obj = obj.parent
                if isinstance(obj, TycoArray):      # we don't consider the list itself the parent for reference purposes
                    obj = obj.parent
                if obj is None:
                    self._error(f"Template '{match.group(0)}' references a parent that does not exist")
            attributes = template_var.split('.')
            if not attributes:
                self._error(f'Empty template content')
            q = collections.deque(attributes)
            while q:
                attr_name = q[0]             # check happy path first
                if hasattr(obj, '__contains__') and attr_name in obj:
                    obj = obj[q.popleft()]
                elif len(q) > 1:
                    attr_with_dot = f'{q.popleft()}.{q.popleft()}'
                    q.appendleft(attr_with_dot)
                elif attributes[0] == 'global':         # local attributes called global get priority
                    obj = self.context._global_instance.inst_kwargs
                    q = collections.deque(attributes[1:])
                else:
                    self._error(f"Template '{match.group(0)}' references unknown attribute '{attr_name}'")
            if obj.field_info.type_name not in ('str', 'int'):
                self._error(f"Template '{match.group(0)}' can only insert strings or integers "
                            f"(got '{obj.field_info.type_name}')")
            return str(obj.as_object())

        rendered = re.sub(self.TEMPLATE_REGEX, template_render, self._as_object)
        rendered = sub_escape_sequences(rendered)
        self._as_object = rendered

    def as_object(self):
        if self._as_object is self._unrendered:     # we use the content before templating has been run
            return self._rendered
        else:
            return self._as_object

    def as_json(self):
        if isinstance(self._as_object, (datetime.date, datetime.time, datetime.datetime)):
            return self._as_object.isoformat()
        elif isinstance(self._as_object, decimal.Decimal):
            return float(self._as_object)
        return self._as_object

    def dump_tyco(self, _dump_schema, show_attr):
        if self._as_object is self._unrendered:
            self.render_templates()
        type_name = self.type_name
        attr_name = self.attr_name
        if self._as_object is None:
            dumped = 'null'
        elif type_name == 'str':
            if not self._as_object:         # empty string
                needs_quotes = True
            elif self._as_object[0].isspace() or self._as_object[-1].isspace():
                needs_quotes = True
            elif any(c in self.special_characters for c in self._as_object):
                needs_quotes = True
            else:
                needs_quotes = False
            if needs_quotes:
                escaped = self._as_object.replace("\\", "\\\\").replace("'", "\\'")
                dumped = f"'{escaped}'"
            else:
                dumped = self._as_object
        elif type_name == 'bool':
            dumped = str(self._as_object).lower()
        elif type_name in ('date', 'time'):
            dumped = self._as_object.isoformat()
        elif type_name == 'datetime':
            dumped = self._as_object.isoformat(sep=' ')
        else:
            dumped = str(self._as_object)
        if show_attr:
            dumped = f'{attr_name}: {dumped}'
        return dumped

    def set_object(self, value):
        self._rendered = value
        self._as_object = value

    @property
    def type_name(self):        # normally set by the schema, but here inferred for the json converter
        if type(self._as_object) == str:
            return 'str'
        if type(self._as_object) == int:
            return 'int'
        if type(self._as_object) == bool:
            return 'bool'
        if type(self._as_object) == float:
            return 'float'
        if type(self._as_object) == decimal.Decimal:
            return 'decimal'
        if type(self._as_object) == datetime.date:
            return 'date'
        if type(self._as_object) == datetime.time:
            return 'time'
        if type(self._as_object) == datetime.datetime:
            return 'datetime'
        if self._as_object is None:
            return 'null'
        raise TycoException(f'Unknown type for {self._as_object!r}')

    def __eq__(self, other):
        return self.as_object() == other.as_object()

    def __hash__(self):
        return hash(self.as_object())

    def __str__(self):
        try:
            type_name = f'{self.field_info.type_name}: '
        except Exception:
            type_name = ''
        content = self.fragment if self._as_object is self._unrendered else self._as_object
        return f'TycoValue({type_name}{content})'

    def __repr__(self):
        return self.__str__()

    def _error(self, message):
        raise TycoParseError(message, self.fragment)


class _Struct:

    """Helper attributes and functions held here to avoid name collisions"""

    _structs = {}        # {type_name : struct}     

    @classmethod
    def create_object(cls, context, type_name, **kwargs):
        if type_name not in cls._structs:
            cls._structs[type_name] = type(type_name, (Struct,), {})
        obj = cls._structs[type_name](**kwargs)
        if (func := getattr(obj, 'validate', None)) and callable(func):
            func()
        return obj


class Struct(types.SimpleNamespace, collections.abc.Mapping):

    """Base class for user-defined objects materialized from Tyco configuration data."""

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(*kwargs)
        if cls.__name__ in _Struct._structs:
            raise TycoException(f'{cls.__name__} struct defined multiple times')
        _Struct._structs[cls.__name__] = cls

    def __getitem__(self, key):
        return self.__dict__[key]

    def __setitem__(self, key, value):
        self.__dict__[key] = value

    def __iter__(self):
        return iter(self.__dict__)

    def __len__(self):
        return len(self.__dict__)


class _JsonConverter:

    @classmethod
    def convert_to_tyco_instance(cls, context, content_dict, type_name):
        if not isinstance(content_dict, dict):
            raise TycoException(f'{path} content must be a dictionary')
        inst_kwargs = cls.create_inst_kwargs(context, content_dict)
        return cls.create_tyco_instance(context, inst_kwargs, type_name)

    @classmethod
    def create_inst_kwargs(cls, context, content_dict):
        inst_kwargs = collections.OrderedDict()
        for attr_name, value in content_dict.items():
            if isinstance(value, dict):
                attr = cls.convert_to_tyco_instance(context, value, attr_name)
            elif isinstance(value, list):
                attr = cls.convert_to_tyco_array(context, value, attr_name)
            else:
                attr = cls.convert_to_tyco_value(context, value, attr_name)
            inst_kwargs[attr_name] = attr
        return inst_kwargs

    @classmethod
    def create_tyco_instance(cls, context, inst_kwargs, type_name):
        schema = cls.create_schema(inst_kwargs, type_name)
        if type_name is None:                       # special case for global instance
            context._structs[type_name].schema = schema
        orig_type_name = type_name
        increment = 0
        while True:
            if type_name not in context._structs:
                context._structs[type_name] = TycoStruct(context, type_name, schema)
                break
            elif cls.check_compatible(schema, context._structs[type_name].schema):      # has side effect 
                break
            type_name = f'{orig_type_name}_{increment}'
            increment += 1
        for attr_name, attr in inst_kwargs.items():
            attr.attr_name = attr_name
        inst = TycoInstance(context, inst_kwargs, type_name)
        context._structs[type_name].instances.append(inst)
        return inst

    @classmethod
    def convert_to_tyco_array(cls, context, values, attr_name):
        elements = []
        for value in values:
            if isinstance(value, dict):
                attr = cls.convert_to_tyco_instance(context, value, attr_name)
            elif isinstance(value, list):
                attr = cls.convert_to_tyco_array(context, value, attr_name)
            else:
                attr = cls.convert_to_tyco_value(context, value, attr_name)
            elements.append(attr)
        attr = TycoArray(context, elements)
        try:
            attr.type_name              # if there are mixed types this will raise
        except Exception:               # and we instead create/return an instance
            inst_kwargs = {f'_{i}' : e for i, e in enumerate(elements)}         #TODO check if struct type_name has collision with base_types
            attr = cls.create_tyco_instance(context, inst_kwargs, attr_name)
        attr.attr_name = attr_name
        return attr

    @classmethod
    def convert_to_tyco_value(cls, context, value, attr_name):
        attr = TycoValue(context)
        attr.set_object(value)
        attr.attr_name = attr_name
        return attr

    @classmethod
    def create_schema(cls, inst_kwargs, type_name):
        schema = collections.OrderedDict()
        for attr_name, attr in inst_kwargs.items():
            type_name = attr.type_name
            is_primary = False
            is_nullable = False
            is_array = isinstance(attr, TycoArray)
            schema[attr_name] = TycoField(type_name, is_primary, is_nullable, is_array)
        return schema

    @classmethod
    def check_compatible(cls, new_schema, old_schema):
        if new_schema.keys() != old_schema.keys():
            return False
        for key, new in new_schema.items():
            old = old_schema[key]
            if 'null' not in (new.type_name, old.type_name):
                if new.is_array != old.is_array:
                    return False
                if new.type_name != old.type_name:
                    return False
        # this part exists to handle when one of the values is null
        for key, new in new_schema.items():
            old = old_schema[key]
            if new == old:
                continue
            # if we've gotten here, it means that the field is now nullable
            if new.type_name == 'null':
                new = new._replace(type_name=old.type_name, is_nullable=True, is_array=old.is_array)
            else:
                old = old._replace(type_name=new.type_name, is_nullable=True, is_array=new.is_array)
            new_schema[key] = new
            old_schema[key] = new
        return True


def load(path: Union[str, pathlib.Path, TextIO, int]) -> 'TycoContext':
    """
    Load Tyco configuration from disk and return the rendered context.

    If `path` is a directory every `*.tyco` file beneath it (recursively) is
    parsed; if it is a single file only that document is processed.  The
    returned context can be interrogated for globals, structs, JSON, or
    converted into concrete Python objects.

    The `path` argument may also be an already-open text stream or a raw file
    descriptor; the loader will read from that stream without touching the
    filesystem again.
    """
    context = TycoContext()
    if isinstance(path, io.TextIOBase):
        TycoLexer.from_text_io_wrapper(context, path)
    elif isinstance(path, int):
        fd = os.fdopen(path, 'r', closefd=False)
        try:
            TycoLexer.from_text_io_wrapper(context, fd)
        finally:
            fd.close()
    else:
        if os.path.isdir(path):
            dir_path = pathlib.Path(path)
            paths = [str(p) for p in dir_path.rglob('*.tyco')]
        else:
            paths = [str(path)]
        for path in paths:
            TycoLexer.from_path(context, path)
    context._render_content()
    return context


def loads(content: str) -> 'TycoContext':
    """
    Parse Tyco configuration provided as a string and return the context.

    This helper mirrors :func:`load` but avoids touching the filesystem,
    which is useful in tests or when generating configuration dynamically.
    """
    context = TycoContext()
    TycoLexer.from_string(context, content)
    context._render_content()
    return context


def loads_from_json(content: str):
    json_content = json.loads(content)
    context = TycoContext()
    type_name = None                # global instance has None for a type_name
    attr = _JsonConverter.convert_to_tyco_instance(context, json_content, type_name)
    context._global_instance = attr
    context._render_content()
    return context


def load_from_json(path: Union[str, pathlib.Path, TextIO, int]) -> 'TycoContext':
    """Load Tyco configuration from a JSON document."""
    if isinstance(path, io.TextIOBase):
        content = path.read()
    elif isinstance(path, int):
        with os.fdopen(path, 'r', closefd=False) as fd:
            content = fd.read()
    else:
        with open(path) as handle:
            content = handle.read()
    return loads_from_json(content)
