#!/usr/bin/env python3

import io
import os
import re
import sys
import types
import string
import decimal
import pathlib
import datetime
import importlib
import collections
import collections.abc
from typing import TextIO, Union


__all__ = ['Struct', 'load', 'loads', 'TycoException', 'TycoParseError']


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

    ire = r'((?!\d)\w+)'            # regex to match identifiers
    GLOBAL_SCHEMA_REGEX = rf'([?])?{ire}(\[\])?\s+{ire}\s*:'
    STRUCT_BLOCK_REGEX  = rf'^{ire}:'
    STRUCT_SCHEMA_REGEX = rf'^\s+([*?])?{ire}(\[\])?\s+{ire}\s*:'
    STRUCT_DEFAULTS_REGEX = rf'\s+{ire}\s*:'
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
        lexer = TycoLexer(context, lines, path='<string>')
        context._path_cache[id(lexer)] = lexer
        lexer.process()
        return lexer

    def __init__(self, context, source_lines, path):
        self.context = context
        self.source_lines = [SourceString(l, self, i, 1) for i, l in enumerate(source_lines, start=1)]
        self.path = path
        self.lines = collections.deque(self.source_lines)       # what we use to do the work
        self.defaults = {}       # {type_name : {attr_name : TycoInstance|TycoValue|TycoArray|TycoReference}}

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
                    self.defaults.setdefault(type_name, {}).update(attr_defaults)
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
        attr, delim = self._load_tyco_attr(attr_name=attr_name)
        is_primary = False
        field_info = TycoField(type_name, attr_name, is_primary, is_nullable, is_array)
        self.context._structs[global_type_name].schema[attr_name] = field_info
        attr.apply_field_info(**field_info)
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
            field_info = TycoField(type_name, attr_name, is_primary, is_nullable, is_array)
            struct.schema[attr_name] = field_info
            default_text = line.split(':', maxsplit=1)[1].lstrip()
            default_content = strip_comments(default_text)
            if default_content:
                self.lines.appendleft(default_text)
                attr, delim = self._load_tyco_attr(attr_name=attr_name)
                attr.apply_field_info(**field_info)
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
                if strip_comments(default_text):
                    self.lines.appendleft(default_text)
                    attr, delim = self._load_tyco_attr(attr_name=attr_name)
                    field_info = struct.schema[attr_name]
                    attr.apply_field_info(**field_info)
                    self.defaults[struct.type_name][attr_name] = attr
                else:
                    self.defaults[struct.type_name].pop(attr_name, None)          # if empty remove previous defaults
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
                    attr, delim = self._load_tyco_attr(good_delim=(',', os.linesep), pop_empty_lines=False)
                    inst_args.append(attr)
                instance_fragment = inst_args[0].fragment if inst_args else line
                default_kwargs = self.defaults[struct.type_name]
                field_info = TycoField(type_name=struct.type_name, attr_name=struct.type_name, is_primary=False, is_nullable=False, is_array=False)
                inst = struct.create_instance(inst_args, default_kwargs, instance_fragment, field_info)
                globals_map = self.context._global_instance.inst_kwargs
                if struct.type_name not in globals_map:
                    globals_map[struct.type_name] = TycoArray(self.context, [], instance_fragment)
                globals_map[struct.type_name].content.append(inst)

    def _load_tyco_attr(self, good_delim=(os.linesep,), bad_delim='', pop_empty_lines=True, attr_name=None):
        bad_delim = set(bad_delim) | set('()[],') - set(good_delim)
        if match := re.match(rf'{self.ire}\s*:\s*', self.lines[0]):     # times don't match this regex
            if attr_name is not None:
                error_text = f'Colon : found in content - enclose in quotes to prevent being used as a field name: {match.groups()[0]}'
                raise TycoParseError(error_text, self.lines[0])
            attr_name = match.groups()[0]
            self.lines[0] = self.lines[0][match.span()[1]:]
            return self._load_tyco_attr(good_delim, bad_delim, pop_empty_lines, attr_name=attr_name)
        ch = self.lines[0][:1]
        if ch == '[':                                               # inline array
            attr = self._load_tyco_array()
            delim = self._strip_next_delim(good_delim)
        elif match := re.match(r'(\w+)\(', self.lines[0]):           # inline instance/reference
            invocation_fragment = self.lines[0]
            type_name = match.groups()[0]
            self.lines[0] = self.lines[0][match.span()[1]:]
            inst_args = self._load_list(')', invocation_fragment)
            if type_name not in self.context._structs or self.context._structs[type_name].primary_keys:
                attr = TycoReference(self.context, inst_args, invocation_fragment)
                attr.apply_field_info(type_name=type_name)
            else:
                default_kwargs = self.defaults[type_name]
                field_info = TycoField(type_name=type_name, is_primary=False, is_nullable=False, is_array=False)
                attr = self.context._structs[type_name].create_instance(inst_args, default_kwargs, invocation_fragment, field_info)
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
        attr.apply_field_info(attr_name=attr_name)
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

    def _load_list(self, closing_char, opening_fragment):
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
            attr, delim = self._load_tyco_attr(good_delims, bad_delims)
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
        self._global_instance = TycoInstance(self, {})        # we put everything into a global instance
        self._structs = {None: TycoStruct(self, None)}        # {type_name : TycoStruct} - global has a type_name of None

    def _render_content(self):
        self._set_parents()
        self._render_base_content()
        self._load_primary_keys()
        self._render_references()
        self._render_templates()

    def _set_parents(self):
        for attr in self._global_instance.inst_kwargs.values():
            attr.set_parent(self._global_instance.inst_kwargs)

    def _render_base_content(self):
        for lexer in self._path_cache.values():
            for type_name, attrs in lexer.defaults.items():     # we render defaults even if they don't get used
                for attr_name, attr in attrs.items():
                    attr.render_base_content()
        for attr in self._global_instance.inst_kwargs.values():
            attr.render_base_content()

    def _load_primary_keys(self):                               # primary keys can only be base types w/o templating
        for attr in self._global_instance.inst_kwargs.values():
            attr.load_primary_keys()

    def _render_references(self):
        for lexer in self._path_cache.values():
            for type_name, attrs in lexer.defaults.items():     # we render defaults even if they don't get used
                for attr_name, attr in attrs.items():
                    attr.render_references()
        for attr in self._global_instance.inst_kwargs.values():
            attr.render_references()

    def _render_templates(self):
        for attr in self._global_instance.inst_kwargs.values():
            attr.render_templates()

    def to_object(self):
        return Struct(**{a : i.to_object() for a, i in self._global_instance.inst_kwargs.items()})

    def to_json(self):
        return {a : i.to_json() for a, i in self._global_instance.inst_kwargs.items()}


class TycoField(types.SimpleNamespace, collections.abc.Mapping):

    def __init__(self, type_name=None, attr_name=None, is_primary=None, is_nullable=None, is_array=None):
        super().__init__()
        self.type_name = type_name
        self.attr_name = attr_name
        self.is_primary = is_primary
        self.is_nullable = is_nullable
        self.is_array = is_array

    def is_complete(self):
        return None not in (self.type_name, self.attr_name, self.is_primary, self.is_nullable, self.is_array)

    def __getitem__(self, key):
        return getattr(self, key)

    def __iter__(self):
        return iter(self.__dict__)

    def __len__(self):
        return len(self.__dict__)

    def __str__(self):
        return (f'TycoField(type_name={self.type_name}, attr_name={self.attr_name}, is_primary={self.is_primary}, '
                f'is_nullable={self.is_nullable}, is_array={self.is_array})')

    def __repr__(self):
        return self.__str__()


class TycoStruct:

    def __init__(self, context, type_name):
        self.context = context
        self.type_name = type_name
        self.schema = collections.OrderedDict()         # {attr_name : TycoField}
        self.mapped_instances = {}                      # {primary_keys : TycoInstance}

    @cached_property
    def attr_names(self):
        return list(self.schema)

    @cached_property
    def primary_keys(self):
        return list(a for a, s in self.schema.items() if s.is_primary)

    def create_instance(self, inst_args, default_kwargs, fragment, field_info):
        local_kwargs = {}
        kwargs_only = False
        for i, attr in enumerate(inst_args):
            if not attr.field_info.attr_name:
                if kwargs_only:
                    raise TycoParseError(f"Positional arguments for '{self.type_name}' must appear before keyed arguments", attr.fragment)
                attr.field_info.attr_name = self.attr_names[i]
            else:
                kwargs_only = True
            local_kwargs[attr.field_info.attr_name] = attr
        inst_fragment = fragment
        if inst_fragment is None and inst_args:
            inst_fragment = getattr(inst_args[0], 'fragment', None)
        if inst_fragment is None:
            raise TycoException(f'Internal parser error: fragment missing for struct {self.type_name}')
        inst_kwargs = {}
        for attr_name in self.schema:
            if attr_name in local_kwargs:
                inst_kwargs[attr_name] = local_kwargs[attr_name]
            elif attr_name in default_kwargs:
                val = default_kwargs[attr_name]
                if isinstance(val, list):
                    inst_kwargs[attr_name] = [v.make_copy() for v in val]
                else:
                    inst_kwargs[attr_name] = val.make_copy()
            else:
                raise TycoParseError(
                    f"Invalid attribute {attr_name} for struct '{self.type_name}': "
                    f"value is required and no default is defined",
                    fragment,
                )
        for attr_name, attr in inst_kwargs.items():
            attr.apply_field_info(**self.schema[attr_name])
        return TycoInstance(self.context, inst_kwargs, inst_fragment, field_info)

    def load_primary_keys(self, inst):
        if not self.primary_keys:
            return
        key = tuple(getattr(inst, k).rendered for k in self.primary_keys)
        if key in self.mapped_instances:
            raise TycoParseError(f"{self.field_info.type_name} with primary key {key} already exists", inst.fragment)
        self.mapped_instances[key] = inst

    def load_reference(self, inst_args):
        inst_kwargs = {}
        kwargs_only = False
        for i, attr in enumerate(inst_args):
            if not attr.field_info.attr_name:
                if kwargs_only:
                    raise TycoParseError(f"Positional reference arguments for '{self.type_name}' must appear before keyed arguments", attr.fragment)
                attr_name = self.primary_keys[i]
            else:
                attr_name = attr.field_info.attr_name
                kwargs_only = True
            field_info = self.schema[attr_name]
            attr.apply_field_info(**field_info)
            attr.render_base_content()
            inst_kwargs[attr_name] = attr
        key = tuple(inst_kwargs[attr_name].rendered for attr_name in self.primary_keys)
        if key not in self.mapped_instances:
            fragment = inst_kwargs[self.primary_keys[0]].fragment
            raise TycoParseError(f"{self.type_name}{key!r} is referenced, but instance can not be found", fragment)
        return self.mapped_instances[key]

    def __str__(self):
        return f'TycoStruct({self.type_name})'

    def __repr__(self):
        return self.__str__()


class TycoInstance:

    def __init__(self, context, inst_kwargs, fragment=None, field_info=None):
        self.context = context
        self.inst_kwargs = inst_kwargs      # {attr_name : TycoValue|TycoInstance|TycoArray|TycoReference}
        self.fragment = fragment
        self.field_info = field_info or TycoField()
        self.parent     = None
        self._as_object = None
        self._as_json   = None

    def make_copy(self):
        inst_kwargs = {a: i.make_copy() for a, i in self.inst_kwargs.items()}
        return self.__class__(self.context, inst_kwargs, self.fragment, self.field_info)

    def apply_field_info(self, **field_info):
        if 'attr_name' in field_info:
            setattr(self.field_info, 'attr_name', field_info.pop('attr_name'))         # set first so that the error is more helpful below
        for attr, val in field_info.items():
            if attr == 'is_array':
                if val is True:
                    raise TycoParseError(f"Field '{self.field_info.attr_name}' is declared as a list, but an object was provided", self.fragment)
            elif attr == 'type_name':
                if self.field_info.type_name not in (val, None):
                    raise TycoParseError(f"Field '{self.field_info.attr_name}' expects an instance of '{self.field_info.type_name}', but '{val}' was provided", self.fragment)
            setattr(self.field_info, attr, val)

    def set_parent(self, parent=None):
        self.parent = parent
        for i in self.inst_kwargs.values():
            i.set_parent(self)

    def render_base_content(self):
        for i in self.inst_kwargs.values():
            i.render_base_content()

    def load_primary_keys(self):
        self.context._structs[self.field_info.type_name].load_primary_keys(self)

    def render_references(self):
        for i in self.inst_kwargs.values():
            i.render_references()

    def render_templates(self):
        for i in self.inst_kwargs.values():
            i.render_templates()

    @property
    def rendered(self):
        return {a : i.rendered for a, i in self.inst_kwargs.items()}

    def to_object(self):
        if self._as_object is None:
            kwargs = {a : v.to_object() for a, v in self.inst_kwargs.items()}
            self._as_object = _Struct.create_object(self.context, self.field_info.type_name, **kwargs)
        return self._as_object

    def to_json(self):
        if self._as_json is None:
            self._as_json = {a : v.to_json() for a, v in self.inst_kwargs.items()}
        return self._as_json

    def __getitem__(self, attr_name):
        return self.inst_kwargs[attr_name]

    def __getattr__(self, attr_name):
        return self.inst_kwargs[attr_name]

    def __str__(self):
        return f'TycoInstance({self.field_info.type_name}, {self.inst_kwargs})'

    def __repr__(self):
        return self.__str__()

    def _error(self, message):
        raise TycoParseError(message, self.fragment)


class TycoReference:                    # Lazy container class to refer to instances

    _unrendered = object()

    def __init__(self, context, inst_args, fragment=None, field_info=None):
        self.context = context
        self.inst_args = inst_args          # [TycoValue,...]
        self.fragment = fragment
        self.field_info = field_info or TycoField()
        self.parent = None
        self.rendered = self._unrendered

    def make_copy(self):
        inst_args = [i.make_copy() for i in self.inst_args]
        return self.__class__(self.context, inst_args, self.fragment, self.field_info)

    def apply_field_info(self, **field_info):
        for attr, val in field_info.items():
            if attr == 'type_name':
                if self.field_info.type_name not in (None, val):
                    error_text = f"Reference for '{self.field_info.attr_name}' expects type '{val}', but '{self.field_info.type_name}' was given"
                    raise TycoParseError(error_text, self.fragment)
            elif attr == 'is_array':
                if val is True:
                    error_text = f"Reference for '{self.field_info.attr_name}' is declared as a list, but a reference was given"
                    raise TycoParseError(error_text, self.fragment)
            setattr(self.field_info, attr, val)

    def set_parent(self, parent):           # not used for anything
        self.parent = parent

    def render_base_content(self):
        pass

    def load_primary_keys(self):
        pass

    def render_references(self):
        if self.rendered is not self._unrendered:
            self._error('Reference was resolved more than once; this indicates a parser bug')
        if self.field_info.type_name not in self.context._structs:
            self._error(f"Unknown struct '{self.field_info.type_name}' referenced")
        struct = self.context._structs[self.field_info.type_name]
        self.rendered = struct.load_reference(self.inst_args)

    def render_templates(self):
        pass

    def _error(self, message):
        raise TycoParseError(message, self.fragment)

    def __getitem__(self, attr_name):
        return self.rendered[attr_name]

    def __getattr__(self, attr_name):
        return self.rendered[attr_name]

    def to_object(self):
        return self.rendered.to_object()

    def to_json(self):
        return self.rendered.to_json()

    def __str__(self):
        return f'TycoReference({self.field_info.type_name}, {self.inst_args}, {self.rendered})'

    def __repr__(self):
        return self.__str__()


class TycoArray:

    def __init__(self, context, content, fragment=None, field_info=None):
        self.context = context
        self.content = content            # [TycoInstance|TycoValue|TycoReference,...]
        self.fragment = fragment
        self.field_info = field_info or TycoField()
        self.parent = None
        self._as_object = None
        self._as_json = None

    def apply_field_info(self, **field_info):           #TODO we should somehow run this at init
        for attr, val in field_info.items():
            if attr == 'is_array':
                if val is False:
                    attr_name = self.field_info.attr_name or '<unknown>'
                    self._error(f"The schema for '{attr_name}' does not indicate this is an array."
                        f" Append [] to the schema definition if '{attr_name}' should be an array.")
            setattr(self.field_info, attr, val)
        for i in self.content:
            kwargs = {'is_nullable' : False, 'is_array' : False, 'is_primary' : False}
            if self.field_info.type_name is not None:
                kwargs['type_name'] = self.field_info.type_name
            if self.field_info.attr_name is not None:
                kwargs['attr_name'] = self.field_info.attr_name
            i.apply_field_info(**kwargs)

    def set_parent(self, parent):
        self.parent = parent
        for i in self.content:
            i.set_parent(parent)            # we ignore the TycoArray object itself for purposes of templating

    def render_base_content(self):
        for i in self.content:
            i.render_base_content()

    def load_primary_keys(self):
        for i in self.content:
            i.load_primary_keys()

    def render_references(self):
        for i in self.content:
            i.render_references()

    def render_templates(self):
        for i in self.content:
            i.render_templates()

    def make_copy(self):
        return self.__class__(self.context, [i.make_copy() for i in self.content], self.fragment, self.field_info)

    @property
    def rendered(self):
        return [i.rendered for i in self.content]

    def to_object(self):
        if self._as_object is None:
            self._as_object = [i.to_object() for i in self.content]
        return self._as_object

    def to_json(self):
        if self._as_json is None:
            self._as_json = [i.to_json() for i in self.content]
        return self._as_json

    def __str__(self):
        return f'TycoArray({self.field_info.type_name} {self.field_info.attr_name}: {self.content})'

    def __repr__(self):
        return self.__str__()

    def _error(self, message):
        raise TycoParseError(message, self.fragment)


class TycoValue:

    TEMPLATE_REGEX = r'\{([\w\.]+)\}'
    base_types = {'str', 'int', 'bool', 'float', 'decimal', 'date', 'time', 'datetime'}
    _unrendered = object()

    def __init__(self, context, fragment=None, field_info=None):
        self.context = context
        self.fragment = fragment
        self.field_info = field_info or TycoField()
        self.parent      = None           # set later
        self.is_literal_str = False
        self.rendered = self._unrendered

    def make_copy(self):
        return self.__class__(self.context, self.fragment, self.field_info)

    def apply_field_info(self, **field_info):
        for attr, val in field_info.items():
            setattr(self.field_info, attr, val)
        if self.field_info.is_array is True and not (self.field_info.is_nullable is True and self.fragment == 'null'):
            self._error(f"Schema indicates that this should be an array, but found a single value for '{self.field_info.attr_name}'")
        if self.field_info.type_name is not None and self.field_info.type_name not in self.base_types:
            self._error(f"Invalid {self.field_info.type_name} type - must be one of: {self.base_types}")

    def set_parent(self, parent):
        self.parent = parent

    def render_base_content(self):
        if not self.field_info.is_complete():
            self._error(f'Internal parser error: attribute metadata missing before rendering: {self.field_info}')
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
        self.rendered = rendered

    def load_primary_keys(self):
        pass

    def render_references(self):
        pass

    def render_templates(self):
        if not self.field_info.type_name == 'str' or self.is_literal_str:
            return
        if self.field_info.is_nullable and self.rendered is None:
            return

        def template_render(match):
            obj = self.parent
            template_var = match.groups()[0]
            if template_var.startswith('..'):       # indicates parent
                template_var = template_var[1:]     # double for parent, triple for parent's parent etc
                while template_var.startswith('.'):
                    obj = obj.parent
                    if obj is None:
                        self._error(
                            f"Template '{match.group(0)}' references a parent that does not exist"
                        )
                    template_var = template_var[1:]     # strip off a leading .
            for i, attr in enumerate(template_var.split('.')):
                try:
                    obj = obj[attr]
                except KeyError:
                    if i == 0 and attr == 'global':
                        obj = self.context._global_instance.inst_kwargs
                    else:
                        self._error(
                            f"Template '{match.group(0)}' references unknown attribute '{attr}'"
                        )
            if obj.field_info.type_name not in ('str', 'int'):
                self._error(
                    f"Template '{match.group(0)}' can only insert strings or integers "
                    f"(got '{obj.field_info.type_name}')"
                )
            return str(obj.rendered)

        rendered = re.sub(self.TEMPLATE_REGEX, template_render, self.rendered)
        rendered = sub_escape_sequences(rendered)
        self.rendered = rendered

    def to_object(self):
        return self.rendered

    def to_json(self):
        if isinstance(self.rendered, (datetime.date, datetime.time, datetime.datetime)):
            return self.rendered.isoformat()
        elif isinstance(self.rendered, decimal.Decimal):
            return float(self.rendered)
        return self.rendered

    def __str__(self):
        return f'TycoValue({self.field_info.type_name}, {self.fragment}, {self.rendered})'

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
        obj.validate()              #TODO is there a better way to avoid collision?
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

    def validate(self):
        pass


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


#def from_json(path):
#    with open(path) as f:
#        content = json.load(f)
#    context = TycoContext()
#    _convert_to_tyco(context, content)
#    return context
#
#
#def _convert_to_tyco(context, content_dict, struct=None):
#    if not isinstance(content, dict):
#        raise TycoException(f'{path} content must be a dictionary')
#    for key, value in content.items():
#        if type(value) is list:
#            if {type(v) for v in value} == {dict}:      # list of objects
#                _load_struct_objects(context, key, value)
#        if type(value) in (int, float, bool, str):
#            ...
#
#def _load_struct_objects(context, type_name, objects):
#    if type_name in context._structs:
#        raise TycoException(f'{type_name} already set up')        # TODO can this come from different sections?
#    struct = type(type_name, (Struct,))
#    all_key_types = None
#    for keyvals in objects:
#        key_types = {k : type(v) for k, v in keyvals.items()}
#        if all_key_types is None:
#            all_key_types = key_types
#        elif (all_keys := set(all_key_types)) != (keys := set(key_types)):
#            raise TycoException(f'Inconsistent keys when processing {type_name}: {all_keys} vs {keys}')
#        elif all_key_types != key_types:
#            differences = {}
#            for k, t in all_key_types.items():                  #TODO support None types
#                if t != key_types[k]:
#                    differences[k] = (t, key_types[k])
#            raise TycoException(f'Type of values of {type_name} not consistent: {differences}')
#
