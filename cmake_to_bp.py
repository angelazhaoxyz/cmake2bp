#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import os
import re
import sys
from typing import Dict, List, Tuple, Optional, Set


def read_text(path):
	with open(path, 'r', encoding='utf-8') as f:
		return f.read()


def write_text(path, content):
	with open(path, 'w', encoding='utf-8') as f:
		f.write(content)


def remove_cmake_comments(text: str) -> str:
	# Remove comments starting with '#' not inside quotes (naive but good enough here)
	lines = text.splitlines()
	stripped = []
	for line in lines:
		in_quote = False
		result_chars = []
		for i, ch in enumerate(line):
			if ch == '"':
				in_quote = not in_quote
			if ch == '#' and not in_quote:
				break
			result_chars.append(ch)
		stripped.append(''.join(result_chars))
	return '\n'.join(stripped)


def parse_paren_blocks(fn_name: str, content: str) -> List[str]:
	# Returns list of argument strings inside fn_name(...)
	pattern = re.compile(r"%s\s*\((.*?)\)" % re.escape(fn_name), re.S)
	return [m.group(1).strip() for m in pattern.finditer(content)]


def split_cmake_args(arg_str: str) -> List[str]:
	# Split by whitespace, honoring quotes
	args = []
	current = []
	in_quote = False
	escape = False
	for ch in arg_str:
		if escape:
			current.append(ch)
			escape = False
			continue
		if ch == '\\':
			escape = True
			continue
		if ch == '"':
			in_quote = not in_quote
			continue
		if not in_quote and ch.isspace():
			if current:
				args.append(''.join(current))
				current = []
			continue
		current.append(ch)
	if current:
		args.append(''.join(current))
	return args


def is_visibility_token(tok: str) -> bool:
	return tok in ("PUBLIC", "PRIVATE", "INTERFACE")


def normalize_path_token(tok: str) -> List[str]:
	"""Extract usable paths from CMake tokens, expanding simple generator expressions.
	Returns zero or more candidate paths.
	"""
	def _normalize_one(raw: str) -> Optional[str]:
		s = raw.strip()
		# Replace known CMake vars to relative
		s = s.replace("${CMAKE_CURRENT_SOURCE_DIR}/", "")
		s = s.replace("${CMAKE_BINARY_DIR}/", "")
		s = s.replace("${CMAKE_CURRENT_SOURCE_DIR}", ".")
		s = s.replace("${CMAKE_BINARY_DIR}", ".")
		# include/${PREFIX} -> include
		s = re.sub(r"^include(?:/[\\$\\{\\}A-Za-z0-9_]+)?$", "include", s)
		# Normalize separators and cleanups
		s = s.replace('\\\
', '/')
		if s.startswith("./"):
			s = s[2:]
		s = re.sub(r"/+$", "", s)
		# Ignore plain variable-only tokens
		if s in ("", ".") or (s.startswith("${") and s.endswith("}")):
			return None
		return s

	# Handle generator expressions like $<BUILD_INTERFACE:foo;bar>
	gen_match = re.match(r"\$<[^:>]+:([^>]+)>", tok)
	if gen_match:
		inner = gen_match.group(1)
		results = []
		for piece in re.split(r"[;]", inner):
			norm = _normalize_one(piece)
			if norm:
				results.append(norm)
		return results
	# Non-generator token
	norm = _normalize_one(tok)
	return [norm] if norm else []


def normalize_lib_token(tok: str, alias_map: Dict[str, str]) -> Optional[str]:
	# Resolve alias
	if tok in alias_map:
		return alias_map[tok]
	# Strip generator and variables
	if tok.startswith("${") and tok.endswith("}"):
		return None
	# Drop visibility
	if is_visibility_token(tok):
		return None
	# CMake imported targets often look like ns::name; use tail part
	if "::" in tok:
		return tok.split("::", 1)[1]
	return tok


class Target(object):
	def __init__(self, name: str, kind: str):
		self.name = name
		self.kind = kind  # 'library' or 'binary'
		self.srcs = []  # type: List[str]
		self.local_include_dirs = []  # type: List[str]
		self.export_include_dirs = []  # type: List[str]
		self.shared_libs = []  # type: List[str]
		self.static_libs = []  # type: List[str]
		self.cflags = []  # type: List[str]
		self.cpp_std = None  # type: Optional[str]
		self.defines = []  # type: List[str]

	def add_srcs(self, items: List[str]):
		for s in items:
			if s and s not in self.srcs:
				self.srcs.append(s)

	def add_local_includes(self, items: List[str]):
		for d in items:
			if d and d not in self.local_include_dirs:
				self.local_include_dirs.append(d)

	def add_export_includes(self, items: List[str]):
		for d in items:
			if d and d not in self.export_include_dirs:
				self.export_include_dirs.append(d)

	def add_shared_libs(self, items: List[str]):
		for lib in items:
			if lib and lib not in self.shared_libs:
				self.shared_libs.append(lib)

	def add_static_libs(self, items: List[str]):
		for lib in items:
			if lib and lib not in self.static_libs:
				self.static_libs.append(lib)

	def add_cflags(self, items: List[str]):
		for flag in items:
			if flag and flag not in self.cflags:
				self.cflags.append(flag)

	def set_cpp_std(self, std: Optional[str]):
		if std:
			self.cpp_std = std


class Converter(object):
	def __init__(self, cmake_text: str):
		self.text = cmake_text
		self.alias_map = {}  # type: Dict[str, str]
		self.targets = {}  # type: Dict[str, Target]

	def get_or_create(self, name: str, kind: str) -> Target:
		if name not in self.targets:
			self.targets[name] = Target(name, kind)
		return self.targets[name]

	def run(self):
		content = remove_cmake_comments(self.text)
		self._parse_add_library(content)
		self._parse_add_executable(content)
		self._parse_target_sources(content)
		self._parse_target_include_directories(content)
		self._parse_target_link_libraries(content)
		self._parse_target_compile_definitions(content)
		self._parse_target_compile_options(content)
		self._parse_set_target_properties(content)

	def _parse_add_library(self, content: str):
		for args_str in parse_paren_blocks('add_library', content):
			args = split_cmake_args(args_str)
			if not args:
				continue
			# ALIAS form: add_library(Alias ALIAS Real)
			if 'ALIAS' in args:
				idx = args.index('ALIAS')
				alias_name = ' '.join(args[:idx]).strip()
				real_name = args[idx + 1] if idx + 1 < len(args) else None
				if real_name:
					self.alias_map[alias_name] = real_name
				continue
			target_name = args[0]
			# Skip optional type token
			srcs = args[1:]
			target = self.get_or_create(target_name, 'library')
			target.add_srcs([s for s in srcs if not s.startswith('${') and not s in ('STATIC','SHARED','MODULE','OBJECT','INTERFACE')])

	def _parse_add_executable(self, content: str):
		for args_str in parse_paren_blocks('add_executable', content):
			args = split_cmake_args(args_str)
			if not args:
				continue
			target_name = args[0]
			srcs = args[1:]
			target = self.get_or_create(target_name, 'binary')
			target.add_srcs([s for s in srcs if not s.startswith('${')])

	def _parse_target_sources(self, content: str):
		for args_str in parse_paren_blocks('target_sources', content):
			args = split_cmake_args(args_str)
			if not args:
				continue
			target_name = args[0]
			# Skip visibility tokens and collect sources
			srcs = [a for a in args[1:] if not is_visibility_token(a) and not a.startswith('${')]
			if target_name in self.targets:
				self.targets[target_name].add_srcs(srcs)

	def _parse_target_include_directories(self, content: str):
		for args_str in parse_paren_blocks('target_include_directories', content):
			args = split_cmake_args(args_str)
			if not args:
				continue
			target_name = args[0]
			if target_name not in self.targets:
				continue
			mode = 'PRIVATE'
			local_dirs = []
			export_dirs = []
			for tok in args[1:]:
				if is_visibility_token(tok):
					mode = tok
					continue
				paths = normalize_path_token(tok)
				if not paths:
					continue
				if mode in ('PRIVATE',):
					local_dirs.extend(paths)
				else:
					# PUBLIC or INTERFACE -> export
					export_dirs.extend(paths)
			# Clean up
			local_dirs = _unique_keep_order([_cleanup_dir(p) for p in local_dirs if p])
			export_dirs = _unique_keep_order([_cleanup_dir(p) for p in export_dirs if p])
			t = self.targets[target_name]
			t.add_local_includes(local_dirs)
			t.add_export_includes(export_dirs)

	def _parse_target_link_libraries(self, content: str):
		for args_str in parse_paren_blocks('target_link_libraries', content):
			args = split_cmake_args(args_str)
			if not args:
				continue
			target_name = args[0]
			if target_name not in self.targets:
				continue
			libs = []
			for tok in args[1:]:
				lib = normalize_lib_token(tok, self.alias_map)
				if lib:
					libs.append(lib)
			libs = _unique_keep_order(libs)
			# Assume shared libs by default
			self.targets[target_name].add_shared_libs(libs)

	def _parse_target_compile_definitions(self, content: str):
		for args_str in parse_paren_blocks('target_compile_definitions', content):
			args = split_cmake_args(args_str)
			if not args:
				continue
			target_name = args[0]
			if target_name not in self.targets:
				continue
			defs = []
			mode = 'PRIVATE'
			for tok in args[1:]:
				if is_visibility_token(tok):
					mode = tok
					continue
				if tok.startswith('${'):
					continue
				if tok.startswith('-D'):
					defs.append(tok)
				else:
					defs.append('-D' + tok)
			self.targets[target_name].add_cflags(defs)

	def _parse_target_compile_options(self, content: str):
		for args_str in parse_paren_blocks('target_compile_options', content):
			args = split_cmake_args(args_str)
			if not args:
				continue
			target_name = args[0]
			if target_name not in self.targets:
				continue
			flags = []
			for tok in args[1:]:
				if is_visibility_token(tok) or tok.startswith('${'):
					continue
				flags.append(tok)
			self.targets[target_name].add_cflags(flags)

	def _parse_set_target_properties(self, content: str):
		for args_str in parse_paren_blocks('set_target_properties', content):
			args = split_cmake_args(args_str)
			if not args or 'PROPERTIES' not in args:
				continue
			idx = args.index('PROPERTIES')
			names = args[:idx]
			props = args[idx + 1:]
			# properties are key value pairs
			prop_map = {}
			for i in range(0, len(props) - 1, 2):
				k = props[i]
				v = props[i + 1]
				prop_map[k] = v
			for name in names:
				if name not in self.targets:
					continue
				std_val = prop_map.get('CXX_STANDARD')
				if std_val and std_val.isdigit():
					self.targets[name].set_cpp_std('c++%s' % std_val)


def _cleanup_dir(path_str: str) -> str:
	p = path_str.strip()
	if p.startswith('./'):
		p = p[2:]
	p = p.replace('\\', '/')
	p = re.sub(r"/+$", "", p)
	return p


def _unique_keep_order(items: List[str]) -> List[str]:
	seen = set()  # type: Set[str]
	result = []
	for it in items:
		if it not in seen:
			seen.add(it)
			result.append(it)
	return result


def to_android_bp(targets: Dict[str, Target]) -> str:
	parts = []
	# Emit libraries first
	for name, t in targets.items():
		if t.kind != 'library':
			continue
		parts.append(_emit_cc_library(t))
	# Then binaries
	for name, t in targets.items():
		if t.kind != 'binary':
			continue
		parts.append(_emit_cc_binary(t))
	return "\n\n".join(parts) + "\n"


def _emit_cc_library(t: Target) -> str:
	indent = "  "
	lines = []
	lines.append("cc_library {")
	lines.append(indent + "name: \"%s\"," % t.name)
	if t.srcs:
		lines.append(indent + "srcs: [")
		for s in t.srcs:
			lines.append(indent * 2 + "\"%s\"," % s)
		lines.append(indent + "],")
	if t.local_include_dirs:
		lines.append(indent + "local_include_dirs: [")
		for d in t.local_include_dirs:
			lines.append(indent * 2 + "\"%s\"," % d)
		lines.append(indent + "];")
		# Soong uses commas; replace accidental semicolon
		lines[-1] = lines[-1].replace('];', '],')
	if t.export_include_dirs:
		lines.append(indent + "export_include_dirs: [")
		for d in t.export_include_dirs:
			lines.append(indent * 2 + "\"%s\"," % d)
		lines.append(indent + "],")
	if t.shared_libs:
		lines.append(indent + "shared_libs: [")
		for lib in t.shared_libs:
			if lib == t.name:
				continue
			lines.append(indent * 2 + "\"%s\"," % lib)
		lines.append(indent + "],")
	if t.cflags:
		lines.append(indent + "cflags: [")
		for f in t.cflags:
			lines.append(indent * 2 + "\"%s\"," % f)
		lines.append(indent + "],")
	if t.cpp_std:
		lines.append(indent + "cpp_std: \"%s\"," % t.cpp_std)
	else:
		lines.append(indent + "cpp_std: \"c++17\",")
	lines.append("}")
	return "\n".join(lines)


def _emit_cc_binary(t: Target) -> str:
	indent = "  "
	lines = []
	lines.append("cc_binary {")
	lines.append(indent + "name: \"%s\"," % t.name)
	if t.srcs:
		lines.append(indent + "srcs: [")
		for s in t.srcs:
			lines.append(indent * 2 + "\"%s\"," % s)
		lines.append(indent + "],")
	if t.shared_libs:
		lines.append(indent + "shared_libs: [")
		for lib in t.shared_libs:
			if lib == t.name:
				continue
			lines.append(indent * 2 + "\"%s\"," % lib)
		lines.append(indent + "],")
	if t.cflags:
		lines.append(indent + "cflags: [")
		for f in t.cflags:
			lines.append(indent * 2 + "\"%s\"," % f)
		lines.append(indent + "],")
	if t.cpp_std:
		lines.append(indent + "cpp_std: \"%s\"," % t.cpp_std)
	else:
		lines.append(indent + "cpp_std: \"c++17\",")
	lines.append("}")
	return "\n".join(lines)


def main():
	parser = argparse.ArgumentParser(description='Convert CMakeLists.txt to Android.bp (basic subset).')
	parser.add_argument('--in', dest='input_path', required=True)
	parser.add_argument('--out', dest='output_path', required=True)
	args = parser.parse_args()

	cmake_text = read_text(args.input_path)
	conv = Converter(cmake_text)
	conv.run()
	bp_text = to_android_bp(conv.targets)
	write_text(args.output_path, bp_text)
	print('Wrote %s' % args.output_path)


if __name__ == '__main__':
	main()