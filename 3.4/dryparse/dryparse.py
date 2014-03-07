#!/usr/bin/env python3
#
# dryparse
#
# A don't-repeat-yourself command-line parser.
#
# TODO:
#  * rename value_usage
#  * when called as a module (python3 -m dryparse <x>) it decorates
#    the callables so that for all arguments it tries eval, and if
#    that works it uses the result, otherwise it fails over to str
#  * --version?
#  * -h / --help ?
#  * in usage: square brackets around optional options/arguments [-h]
#  * in usage: angle brackets around positional arguments
#  * python ideas:
#      a typeerror should have an optional iterable of base classes of
#      types it accepts
#
#
# License
# =======
#
# Copyright 2012-2013 Larry Hastings
#
# This software is provided 'as-is', without any express or implied
# warranty. In no event will the authors be held liable for any damages
# arising from the use of this software.
#
# Permission is granted to anyone to use this software for any purpose,
# including commercial applications, and to alter it and redistribute it
# freely, subject to the following restrictions:
#
# 1. The origin of this software must not be misrepresented; you must not
# claim that you wrote the original software. If you use this software
# in a product, an acknowledgment in the product documentation would be
# appreciated but is not required.
#
# 2. Altered source versions must be plainly marked as such, and must not be
# misrepresented as being the original software.
#
# 3. This notice may not be removed or altered from any source
# distribution.
#


import collections
import inspect
import itertools
import shlex
import sys
import textwrap
import types
import unittest

__all__ = []
def all(fn):
    __all__.append(fn)
    return fn

class Unspecified:
    def __repr__(self):
        return '<Unspecified>'
    def __bool__(self):
        return False

unspecified = Unspecified()

def option_value_usage_formatter(name):
    return " " + name.upper()



def column_wrapper(prefix, words, *,
    min_column=12, max_column=40, right_margin=79):
    """
    Formats text in a pleasing format.

    "prefix" can be either a string or an iterable of strings.

    "words" should be an iterable of strings that have
    already been broken up at word boundaries.
    An empty string in the words iterable inserts
    a newline in the output.  ()

    The output looks something like this:

        prefix here         words here which are wrapped to
                            multiple lines in a pleasing way.
    """

    lines = []
    line_prefixes = []

    if isinstance(prefix, str):
        line = prefix
    else:
        prefixes = list(reversed(prefix))
        line = ''
        indent = ' ' * 8
        add_space = False
        while prefixes:
            prefix = prefixes.pop()
            test = line
            if add_space:
                test += ' '
            else:
                add_space = True
            test += prefix
            if len(test) > right_margin:
                prefixes.append(prefix)
                lines.append(line)
                line = indent
                first = True
            line = test

    if len(line) < max_column:
        column = max(min_column, len(line))
        line += ' ' * (column - len(line))
        line_prefixes.append(line)
    else:
        column = max_column
        lines.append(line)

    empty_prefixes = itertools.repeat(' ' * column)
    prefixes = itertools.chain(line_prefixes, empty_prefixes)
    words = list(reversed(words))

    if not words:
        for prefix in line_prefixes:
            lines.append(prefix)

    while words:
        test = line = next(prefixes)
        is_first_word = True
        while words:
            o = words.pop()
            if not o:
                break
            spacer = '  ' if line.endswith('.') else ' '
            test = line + spacer + o
            if len(test) > right_margin:
                if is_first_word:
                    line = test
                else:
                    words.append(o)
                break
            else:
                line = test
                is_first_word = False
        lines.append(line)

    return '\n'.join(lines).rstrip()


class _column_wrapper_splitter:

    def __init__(self, tab_width):
        self.next(self.state_initial)
        self.words = []
        self.hopper = []
        self.emit = self.hopper.append
        self.col = self.next_col = 0
        self.line = self.next_line = 0

    def newline(self):
        assert not self.hopper, "Emitting newline while hopper is not empty!"
        self.words.append('')

    def empty_hopper(self):
        if self.hopper:
            self.words.append(''.join(self.hopper))
            self.hopper.clear()

    def next(self, state, c=None):
        self.state = state
        if c is not None:
            self.state(c)

    def write(self, c):
        if c in '\t\n':
            if c == '\t':
                self.next_col = col + tab_width - (col % tab_width)
            else:
                self.next_col = 0
                self.next_line = self.line + 1
        else:
            self.next_col = self.col + 1

        self.state(c)

        self.col = self.next_col
        self.line = self.next_line

    def close(self):
        self.empty_hopper()

    def state_paragraph_start(self, c):
        if c.isspace():
            return
        if self.col >= 4:
            next = self.state_code_line_start
        else:
            next = self.state_in_paragraph
        self.next(next, c)

    state_initial = state_paragraph_start

    def state_code_line_start(self, c):
        if c.isspace():
            if c == '\n':
                self.newline()
                self.next(self.state_paragraph_start)
            return
        if self.col < 4:
            raise ValueError("Can't outdent past 4 in a code paragraph! (line " + str(self.line) + " col " + str(self.col) + ")")
        self.emit(' ' * self.col)
        self.next(self.state_in_code, c)

    def state_in_code(self, c):
        if c.isspace():
            if c == '\n':
                self.empty_hopper()
                self.newline()
                self.next(self.state_code_line_start)
            else:
                self.emit(' ' * (self.next_col - self.col))
        else:
            self.emit(c)

    def state_paragraph_line_start(self, c):
        if not c.isspace():
            return self.next(self.state_in_paragraph, c)
        if c == '\n':
            self.newline()
            self.newline()
            self.next(self.state_paragraph_start)

    def state_in_paragraph(self, c):
        if not c.isspace():
            self.emit(c)
            return

        self.empty_hopper()
        if c == '\n':
            self.next(self.state_paragraph_line_start)


def column_wrapper_split(s, *, tab_width=8):
    """
    Splits up a string into individual words, suitable
    for feeding into column_wrapper().

    Paragraphs indented by four spaces or more preserve
    whitespace; internal whitespace is preserved, and the
    newline is preserved.  (This is for code examples.)

    Paragraphs indented by less than four spaces will be
    broken up into individual words.
    """
    cws = _column_wrapper_splitter(tab_width)
    for c in s:
        cws.write(c)
    cws.close()
    return cws.words


@all
class DryArgument:
    type = None
    doc = None
    multiple = False
    value = unspecified
    value_usage = None

    def __repr__(self):
        a = ['<DryArgument ', self.name]
        add = a.append
        add(self.usage())
        if self.multiple:
            add(' *')
        add(' ')
        add(repr(self.type))
        add(' =')
        add(repr(self.default))

        add('>')
        return ''.join(a)

    def usage(self, *, first_line=True):
        if not self.options:
            return name
        a = []
        add = a.append

        if first_line:
            add('[')
            separator = '|'
        else:
            separator = ', '

        add_separator = False
        for name in sorted(self.options):
            if add_separator:
                add(separator)
            else:
                add_separator = True
            add('-')
            if len(name) > 1:
                add('-')
            add(name)
        if self.type != bool:
            add('=')
            add(self.value_usage)
        if first_line:
            add(']')
        return ''.join(a)

    def set_value(self, value):
        value = self.type(value)
        if self.multiple:
            self.value.append(value)
        else:
            self.value = value

    def __init__(self, name, default, annotations, *, is_option=False):
        self.name = name
        self.default = default
        self.options = set()
        if is_option:
            self.options.add(name)

        for a in annotations or ():
            if callable(a):
                assert self.type is None
                self.type = a
                continue
            if isinstance(a, str):
                if a.startswith('-'):
                    assert is_option, "positional argument " + repr(name) + " cannot be an option (only kwonly arguments can be options)"
                    # options
                    for option in a.strip().split():
                        if option == '*':
                            assert self.default is unspecified, "You can't specify a default for an option that accepts multiple values. (" + repr(name) + ")"
                            self.value = self.default = []
                            self.multiple = True
                            continue
                        if option.startswith('='):
                            self.value_usage = option[1:]
                        if option in {'-', '--'}:
                            continue
                        assert option.startswith('-'), "Illegal field in annotation list of options: '" + option + "'"
                        assert (len(option) == 2) or (option.startswith('--')), "Illegal field in annotation list of options: '" + option + "', but only single-letter options can use one dash"
                        stripped = option.lstrip('-')
                        assert stripped not in self.options, "Option '" + option + "' specified more than once!"
                        self.options.add(stripped)
                else:
                    assert self.doc is None
                    self.doc = a
                continue
            assert None, "Unknown annotation for " + name

        if self.type is None:
            valid_default = self.default is not unspecified
            inferred = type(self.default) if valid_default else None
            default_type = bool if self.options else str

            self.type = inferred or default_type

        if self.default is unspecified and self.options:
            self.default = self.type()

        if is_option and (self.type is not bool) and (not self.value_usage):
            self.value_usage = option_value_usage_formatter(self.type.__name__)

        # print("DryArgument name", repr(name), "type", self.type, "default", repr(default), "annotations", repr(annotations), "is_option", is_option )

class OptionError(RuntimeError):
    pass

@all
class DryCommand:

    def __repr__(self):
        a = ['<DryCommand ', self.name, ' -> ']
        add = a.append

        [add(option) for option in self.options]

        add('>')
        return ''.join(a)

    has_options = has_arguments = False

    def __init__(self, name, callable):
        self.name = name
        self.callable = callable
        self.options = {}
        self.all_arguments = []
        self.arguments = []
        self.doc = callable.__doc__ or ''
        self.star_args = None

        try:
            i = inspect.getfullargspec(callable)
        except TypeError:
            sys.exit("Can't add " + repr(name) + ", callable isn't introspectable " + repr(callable))
        if isinstance(callable, types.MethodType):
            i.args.pop(0)

        # i.defaults is kind of silly
        # convert it to a straight-up tuple
        # with unspecified for unspecified fields
        if i.defaults is None:
            defaults = (unspecified,) * len(i.args)
        else:
            defaults = ((unspecified,) * (len(i.args) - len(i.defaults))) + i.defaults

        def add(name, default, i, *, is_option=False):
            argument = DryArgument(name, default, i.annotations.get(name), is_option=is_option)
            self.all_arguments.append(argument)
            assert int(is_option) ^ (not int(bool(argument.options)))
            if argument.options:
                for option in argument.options:
                    assert option not in self.options
                    self.options[option] = argument
                    self.has_options = True
            else:
                self.arguments.append(argument)
                self.has_arguments = True

        for name, default in zip(i.args, defaults):
            add(name, default, i, is_option=False)

        if i.varargs:
            self.star_args = DryArgument(i.varargs, unspecified, i.annotations.get(i.varargs))
            self.has_arguments = True

        kwonlydefaults = i.kwonlydefaults or {}
        for name in i.kwonlyargs or ():
            default = kwonlydefaults.get(name, unspecified)
            add(name, default, i, is_option=True)

    def _usage_first_line(self, global_handler):
        """
        Everything after the command name.
        """

        def format_options_for_first_line(command):
            if not command:
                return []

            seen = set()

            short_options = []
            long_options = []
            for name in sorted(command.options):
                value = command.options[name]
                if value in seen:
                    continue
                if (len(value.options) == 1 and
                    value.type is bool and
                    len(name) == 1):
                    short_options.append(name)
                else:
                    long_options.append(value.usage(first_line=True))
                seen.add(value)

            if short_options:
                long_options.insert(0, '[-' + ''.join(short_options) + ']')
            return long_options

        global_options = format_options_for_first_line(global_handler)
        options = format_options_for_first_line(self)

        arguments = []
        unwind = 0
        for argument in self.arguments:
            if argument.default is not unspecified:
                arguments.append('[')
                unwind += 1
            arguments.append(argument.name)

        if self.star_args:
            arguments.append('[...]')

        arguments.append(']' * unwind)

        prefix = ["Usage:", sys.argv[0]]
        prefix.extend(global_options)
        prefix.append(self.name)

        words = []
        words.extend(options)
        words.extend(arguments)

        return column_wrapper(prefix, words)

    def _usage(self, *, global_handler=None, short=False, error=None):
        short = bool(short or error)
        output = []
        def print(*a):
            s = " ".join([str(x) for x in a])
            output.append(s)
        lines = []
        summary_line = ''
        if self.doc:
            lines = [x.rstrip() for x in textwrap.dedent(self.doc.expandtabs()).split('\n')]
            while lines and not lines[0]:
                del lines[0]
            if lines:
                summary_line = lines[0]
                del lines[0]
                while lines and not lines[0]:
                    del lines[0]
                while lines and not lines[-1].lstrip():
                    lines.pop()

        if error:
            print(error)

        print(self._usage_first_line(global_handler))

        if error or short:
            print('Try "' + sys.argv[0] + ' help ' + self.name + '" for more information.')
            return '\n'.join(output)

        print()
        print(summary_line)

        # global options
        if global_handler:
            options = {}
            longest_option = 0
            for argument in global_handler.all_arguments:
                if argument.options:
                    dashed_options = [ '-' + ('-' if len(option) > 1 else '') + option for option in argument.options]
                    all_options = ', '.join(sorted(dashed_options))
                    longest_option = max(longest_option, len(all_options))
                    options[all_options] = argument
            print()
            print("Global options:")
            min_column = longest_option + 4
            for all_options in sorted(options):
                argument = options[all_options]
                prefix = "  " + all_options
                split_doc = column_wrapper_split(argument.doc)
                formatted = column_wrapper(prefix, split_doc, min_column=min_column)
                print(formatted)

        options = {}
        longest_option = longest_positional = 0
        for argument in self.all_arguments:
            if argument.options:
                dashed_options = [ '-' + ('-' if len(option) > 1 else '') + option for option in argument.options]
                all_options = ', '.join(sorted(dashed_options))
                longest_option = max(longest_option, len(all_options))
                options[all_options] = argument
            else:
                longest_positional = max(longest_positional, len(argument.name))
        if abs(longest_positional - longest_option) < 3:
            longest_positional = longest_option = max(longest_positional, longest_option)
        if options:
            print()
            print("Options:")
            min_column = longest_option + 4
            for all_options in sorted(options):
                argument = options[all_options]
                prefix = "  " + all_options
                split_doc = column_wrapper_split(argument.doc or '')
                formatted = column_wrapper(prefix, split_doc, min_column=min_column)
                print(formatted)
        if self.arguments:
            print()
            print("Arguments:")
            min_column = longest_positional + 2
            for argument in self.arguments:
                prefix = "  " + argument.name
                split_doc = column_wrapper_split(argument.doc or '')
                formatted = column_wrapper(prefix, split_doc, min_column=min_column)
                print(formatted)
            if self.star_args:
                print("  [" + self.star_args.name + "...]")

        if lines:
            print()
            for line in lines:
                print(" ", line)

        return '\n'.join(output)

    def usage(self, *, error=None):
        print(self._usage(error=error))

    def __call__(self, argv, return_arguments=False):
        seen = set()
        needs_value = None

        def analyze_option(option):
            argument = self.options[option]
            return argument, argument.type is bool

        def handle_option(option, value):
            nonlocal self
            nonlocal needs_value
            argument, is_bool = analyze_option(option)
            if is_bool:
                if value is unspecified:
                    if argument.value is not unspecified:
                        value = not argument.value
                    elif argument.default is not unspecified:
                        value = not argument.default
                    else:
                        value = True
                argument.set_value(value)
            else:
                if value is unspecified:
                    needs_value = argument
                else:
                    argument.set_value(value)
            seen.add(option)

        arguments = []
        all_positional = False
        while argv:
            a = argv.pop(0)
            if all_positional:
                arguments.append(a)
                continue
            if needs_value:
                needs_value.set_value(a)
                needs_value = None
                continue
            if a == '--':
                all_positional = True
                continue
            if a.startswith('-'):
                if '=' in a:
                    a, _, value = a.partition('=')
                    a = a.strip()
                    value = value.strip()
                    assert a.startswith('--') or (len(a) == 2), "string " + repr(a) + " isn't double-dash nor -x"
                else:
                    value = unspecified
                if a.startswith('--'):
                    option = a[2:]
                    if option not in self.options:
                        raise OptionError('Unknown option "' + a + '".')
                    handle_option(option, value)
                else:
                    single_letters = []
                    for c in a[1:]:
                        if c not in self.options:
                            raise OptionError('Unknown option "' + a + '".')
                        arg, is_bool = analyze_option(c)
                        single_letters.append([c, unspecified])
                        if not is_bool:
                            break
                    remaining = a[len(single_letters)+1:]
                    if remaining or (value is not unspecified):
                        remaining_str = "".join(remaining)
                        if value is not unspecified:
                            if remaining_str:
                                remaining_str += '=' + value
                            else:
                                remaining_str = value
                        single_letters[-1][1] = remaining_str
                    for c, value in single_letters:
                        handle_option(c, value)
                continue
            arguments.append(a)

        star_args = []
        if not return_arguments:
            i = -1
            for i, (argument, a) in enumerate(zip(self.arguments, arguments)):
                t = argument.type or str
                argument.value = t(a)
            if self.star_args:
                t = self.star_args.type
                star_args = [t(x) for x in arguments[i + 1:]]

        final_kwargs = {}
        final_args = []
        for a in self.all_arguments:
            if a.value == unspecified:
                a.value = a.default
            if a.options:
                final_kwargs[a.name] = a.value
            else:
                final_args.append(a.value)
        if star_args:
            final_args.extend(star_args)

        # print("DEEBUG EENFO:")
        # print("argv", argv)
        # for i, a in enumerate(self.all_arguments):
        #   print("all_arguments[", i, '] =', a)
        # print("final_args", final_args)
        # print("final_kwargs", final_kwargs)

        needed = len(list(filter(lambda x: x == unspecified, final_args)))
        if needed:
            specified = len(arguments)
            error = ["Not enough arguments."]
            if specified:
                error.extend((" " + str(specified + needed), "required, but only", str(specified), "specified."))
            return self.usage(error=" ".join(error))
        assert unspecified not in final_args

        return_value = self.callable(*final_args, **final_kwargs)
        if return_arguments:
            return arguments
        return return_value

@all
def ignore(callable):
    callable.__dryparse_use__ = False
    return callable

@all
def command(callable):
    callable.__dryparse_use__ = True
    return callable


@all
class DryParse:

    def __init__(self):
        self.commands = {}
        self.doc = ""
        self.add(self.help, "help")

    _global_handler = None

    @property
    def global_handler(self):
        return self._global_handler

    @global_handler.setter
    def global_handler(self, value):
        if not isinstance(value, DryCommand):
            value = DryCommand('', value)
        self._global_handler = value

    def add_raw(self, o, name=None):
        assert callable(o)
        name = name or o.__name__
        self.commands[name] = o

    def add(self, o, name=None):
        assert callable(o)
        name = name or o.__name__
        self.commands[name] = DryCommand(name, o)

    def __setitem__(self, name, value):
        self.add(name, value)

    def test(self, o, name=None):
        if not callable(o):
            return False

        # if it's been explicitly labeled, obey that
        value = getattr(o, '__dryparse_use__', None)
        if value is not None:
            return value

        #       * no if it starts and ends with '__'
        name = name or o.__name__
        if name.startswith('_'):
            return False

        return None

    def update(self, o, test=None, default=None):
        if o is None:
            return

        test = test or self.test

        def NoneToTrue(value):
            if value is None:
                return True
            return value

        if isinstance(o, collections.Mapping):
            if default is None:
                default = True
            for name, value in o.items():
                if NoneToTrue(test(value, name=name)):
                    self.add(value, name=name)
            return

        if isinstance(o, collections.Iterable):
            if default is None:
                default = True
            for value in o:
                if NoneToTrue(test(value)):
                    self.add(value)
            return

        def NoneToFalse(value):
            if value is None:
                return True
            return value

        for name in dir(o):
            value = getattr(o, name)
            if NoneToFalse(test(value, name)):
                self.add(value, name=name)

    def usage(self, *, argv=(), error=None):
        print(self._usage(argv=argv, error=error))

    def _usage(self, *, argv=(), error=None):
        output = []
        def print(*a):
            output.append(' '.join(str(x) for x in a))

        assert not (argv and error)
        if error:
            print(error)
        if argv:
            command = argv[0]
            if command not in self.commands:
                return self.usage(error="Unknown command " + repr(command))
            if command == 'help':
                return self.usage()
            return self.commands[command]._usage()

        have_global_options = self.global_handler and self.global_handler.options
        have_commands = self.commands
        have_global_arguments = (not have_commands) and self.global_handler and self.global_handler.have_arguments
        have_options = any(command.has_options for command in self.commands.values())
        have_arguments = any(command.has_arguments for command in self.commands.values())

        a = ["Usage:", sys.argv[0]]
        add = a.append
        if have_global_options:
            add("[options]")
        if have_global_arguments:
            add("arguments")
        if have_commands:
            add("command")
            if have_options:
                add("[options]")
            if have_arguments:
                add("[arguments ...]")
        print(' '.join(a))
        if self.commands:
            print('Try "' + sys.argv[0] + ' help" for a list of commands.')

        return '\n'.join(output)

    def print_commands(self):
        if not self.commands:
            return
        print("Supported commands:")
        commands = sorted(self.commands)
        longest = len('help')
        for name in commands:
            longest = max(longest, len(name))
        for name in commands:
            if name == 'help':
                print(" ", "help".ljust(longest), " List all commands, or show usage on a specific command.")
                continue
            command = self.commands[name]
            first_line = command.doc.strip().split('\n')[0].strip()
            print(" ", name.ljust(longest), "", first_line)

    def help(self, command=''):
        if command and (command != 'help'):
            if command in self.commands:
                return self.commands[command]._usage(global_handler=self.global_handler)
            print('Unknown command "' + command + '".')
            print()
        self.print_commands()
        print()
        print('Use "' + sys.argv[0] + ' help command" for help on a specific command.')

    def main(self, argv=None):
        return_value = None
        if argv == None:
            argv = sys.argv[1:]

        if self.global_handler:
            have_commands = bool(self.commands)
            if have_commands:
                global_argv = []
                while argv and argv[0].startswith('-'):
                    global_argv.append(argv.pop(0))
            else:
                global_argv = argv

            if global_argv:
                try:
                    return_value = self.global_handler(global_argv, return_arguments=have_commands)
                except OptionError as e:
                    # try to find the command
                    command = None
                    for s in argv:
                        if s.startswith('-'):
                            continue
                        command = self.commands.get(s, None)
                        break

                    if command:
                        return command.usage(error=str(e))
                    return self.usage(error=str(e))

                if have_commands:
                    argv = return_value
                else:
                    return return_value

        if not self.commands:
            return return_value

        if not argv:
            return self.usage(error="No command specified.")

        command_str = argv.pop(0)
        command = self.commands.get(command_str, None)
        if not command:
            if command_str == "help":
                return self.usage(argv=argv)
            return self.usage(error="Command " + repr(command_str) + " not recognized.")
        try:
            return command(argv)
        except OptionError as e:
            return command.usage(error=str(e))


class UnitTests(unittest.TestCase):

    def setUp(self):
        class Commands:
            pass
        self.C = Commands
        self.c = Commands()
        self.dp = DryParse()

    def add(self, callable):
        name = callable.__name__
        setattr(self.C, name, callable)
        self.dp.add(getattr(self.c, name))

    def main(self, cmdline):
        split = shlex.split(cmdline)
        self.dp.main(split)

    def test_basics(self):
        c = self.c
        dp = self.dp
        self.assertTrue('command1' not in dp._usage())
        self.assertTrue('command2' not in dp._usage())
        def command1(self, a, b):
            self.a = a
            self.b = b
        self.add(command1)
        self.assertTrue('command1'     in dp._usage())
        self.assertTrue('command2' not in dp._usage())
        def command2(self, a, b):
            self.a = a
            self.b = b
        self.add(command2)
        self.assertTrue('command1'     in dp._usage())
        self.assertTrue('command2'     in dp._usage())
        c.a = c.b = 0
        self.main('command1 22 33')
        self.assertEqual(c.a, '22')
        self.assertEqual(c.b, '33')

    def test_function_docs(self):
        def command(self, a):
            "box turtle"
            pass
        self.add(command)
        self.assertTrue('box turtle' in self.dp._usage())

    def test_argument_docs(self):
        def command3(self, a:{'lagomorph'}):
            pass
        self.add(command3)
        self.assertTrue('lagomorph' in self.dp._usage(argv=['command3']))

    def test_callable_annotations(self):
        def cast_fn(s=None):
            return 12345
        def callable_annotations(self, a:{cast_fn}=11111):
            self.value = a

        def reset():
            self.c.value = None
            self.add(callable_annotations)

        reset()
        self.main('callable_annotations abc')
        self.assertEqual(self.c.value, 12345)

        reset()
        self.main('callable_annotations')
        self.assertEqual(self.c.value, 11111)

    def test_long_dashed_annotations(self):
        def long_dashed_annotations(self, a, *, debug):
            self.debug = debug
            self.a = a

        def reset():
            self.c.debug = self.c.a = None
            self.add(long_dashed_annotations)

        reset()
        self.main('long_dashed_annotations abc')
        self.assertEqual(self.c.a, 'abc')
        self.assertEqual(self.c.debug, False)
        reset()
        self.main('long_dashed_annotations --debug def')
        self.assertEqual(self.c.a, 'def')
        self.assertEqual(self.c.debug, True)
        reset()
        self.main('long_dashed_annotations ghi')
        self.assertEqual(self.c.a, 'ghi')
        self.assertEqual(self.c.debug, False)

    def test_short_dashed_annotations(self):
        def test_short_dashed_annotations(self, a, *, n, q):
            self.a = a
            self.n = n
            self.q = q
        def reset():
            self.c.a = self.c.n = self.c.q = None
            self.dp.commands = {}
            self.add(test_short_dashed_annotations)
        reset()
        self.main('test_short_dashed_annotations abc')
        self.assertEqual(self.c.a, 'abc')
        self.assertEqual(self.c.n, False)
        self.assertEqual(self.c.q, False)
        reset()
        self.main('test_short_dashed_annotations -q ducks')
        self.assertEqual(self.c.a, 'ducks')
        self.assertEqual(self.c.n, False)
        self.assertEqual(self.c.q, True)
        reset()
        self.main('test_short_dashed_annotations -n garofalo')
        self.assertEqual(self.c.a, 'garofalo')
        self.assertEqual(self.c.n, True)
        self.assertEqual(self.c.q, False)
        reset()
        self.main('test_short_dashed_annotations -nq sassy')
        self.assertEqual(self.c.a, 'sassy')
        self.assertEqual(self.c.n, True)
        self.assertEqual(self.c.q, True)
        reset()
        self.main('test_short_dashed_annotations -qn boots')
        self.assertEqual(self.c.a, 'boots')
        self.assertEqual(self.c.n, True)
        self.assertEqual(self.c.q, True)

    def test_short_dashed_with_value(self):
        def short_dashed_with_value(self, a, *, n:{int}, q:{int}):
            self.a = a
            self.n = n
            self.q = q

        def reset():
            self.dp.commands = {}
            self.c.a = self.c.b = self.c.n = self.c.q = None
            self.add(short_dashed_with_value)


        reset()
        self.main('short_dashed_with_value abc')
        self.assertEqual(self.c.a, 'abc')
        self.assertEqual(self.c.n, 0)
        self.assertEqual(self.c.q, 0)

        reset()
        self.main('short_dashed_with_value -n=5 abc')
        self.assertEqual(self.c.a, 'abc')
        self.assertEqual(self.c.n, 5)
        self.assertEqual(self.c.q, 0)

        reset()
        self.main('short_dashed_with_value -n 5 abc')
        self.assertEqual(self.c.a, 'abc')
        self.assertEqual(self.c.n, 5)
        self.assertEqual(self.c.q, 0)

        reset()
        self.main('short_dashed_with_value -n5 abc')
        self.assertEqual(self.c.a, 'abc')
        self.assertEqual(self.c.n, 5)
        self.assertEqual(self.c.q, 0)

        reset()
        self.main('short_dashed_with_value -q=3 abc')
        self.assertEqual(self.c.a, 'abc')
        self.assertEqual(self.c.n, 0)
        self.assertEqual(self.c.q, 3)

        reset()
        self.main('short_dashed_with_value -q 3 abc')
        self.assertEqual(self.c.a, 'abc')
        self.assertEqual(self.c.n, 0)
        self.assertEqual(self.c.q, 3)

        reset()
        self.main('short_dashed_with_value -q 3 -n=5 abc')
        self.assertEqual(self.c.a, 'abc')
        self.assertEqual(self.c.n, 5)
        self.assertEqual(self.c.q, 3)

        reset()
        def short_dashed_with_value_2(self, *, a, b, n:{int}=0):
            self.a = a
            self.b = b
            self.n = n
        self.add(short_dashed_with_value_2)
        self.main("short_dashed_with_value_2 -ban5")
        self.assertEqual(self.c.a, True)
        self.assertEqual(self.c.b, True)
        self.assertEqual(self.c.n, 5)

    def test_long_dashed_with_value(self):
        def long_dashed_with_value(self, a, *, debug:{int}=0, quiet:{int}=0):
            self.a = a
            self.debug = debug
            self.quiet = quiet

        def reset():
            self.c.a = self.c.debug = self.c.quiet = None
            self.add(long_dashed_with_value)

        reset()
        self.main('long_dashed_with_value abc')
        self.assertEqual(self.c.a, 'abc')
        self.assertEqual(self.c.debug, 0)
        self.assertEqual(self.c.quiet, 0)

        reset()
        self.main('long_dashed_with_value --debug=5 abc')
        self.assertEqual(self.c.a, 'abc')
        self.assertEqual(self.c.debug, 5)
        self.assertEqual(self.c.quiet, 0)

        reset()
        self.main('long_dashed_with_value --debug 5 abc')
        self.assertEqual(self.c.a, 'abc')
        self.assertEqual(self.c.debug, 5)
        self.assertEqual(self.c.quiet, 0)

        reset()
        self.main('long_dashed_with_value --quiet=3 abc')
        self.assertEqual(self.c.a, 'abc')
        self.assertEqual(self.c.debug, 0)
        self.assertEqual(self.c.quiet, 3)

        reset()
        self.main('long_dashed_with_value --quiet 3 abc')
        self.assertEqual(self.c.a, 'abc')
        self.assertEqual(self.c.debug, 0)
        self.assertEqual(self.c.quiet, 3)

        reset()
        self.main('long_dashed_with_value --quiet 3 --debug=5 abc')
        self.assertEqual(self.c.a, 'abc')
        self.assertEqual(self.c.debug, 5)
        self.assertEqual(self.c.quiet, 3)

    def test_multiple_options_as_boolean(self):
        def reset():
            self.c.quiet = None
            self.add(multiple_options_as_boolean)

        def multiple_options_as_boolean(self, *, quiet:{'-q --shut-up -x'}):
            self.quiet = quiet

        reset()
        self.main('multiple_options_as_boolean')
        self.assertEqual(self.c.quiet, False)
        reset()
        self.main('multiple_options_as_boolean -q')
        self.assertEqual(self.c.quiet, True)
        reset()
        self.main('multiple_options_as_boolean --quiet')
        self.assertEqual(self.c.quiet, True)
        reset()
        self.main('multiple_options_as_boolean --shut-up')
        self.assertEqual(self.c.quiet, True)
        reset()
        self.main('multiple_options_as_boolean -x')
        self.assertEqual(self.c.quiet, True)
        reset()
        self.main('multiple_options_as_boolean')
        self.assertEqual(self.c.quiet, False)

    def test_multiple_options_as_type(self):
        def multiple_options_as_type(self, *, value:{'-v --thingy -y', str}='unset'):
            self.value = value

        def reset():
            self.c.value = None
            self.add(multiple_options_as_type)

        reset()
        self.main('multiple_options_as_type')
        self.assertEqual(self.c.value, 'unset')

        reset()
        self.main('multiple_options_as_type -v 123')
        self.assertEqual(self.c.value, '123')
        reset()
        self.main('multiple_options_as_type --value 456')
        self.assertEqual(self.c.value, '456')
        reset()
        self.main('multiple_options_as_type --thingy 789')
        self.assertEqual(self.c.value, '789')
        reset()
        self.main('multiple_options_as_type -y 101112')
        self.assertEqual(self.c.value, '101112')

        reset()
        self.main('multiple_options_as_type -v=123')
        self.assertEqual(self.c.value, '123')
        reset()
        self.main('multiple_options_as_type --value=456')
        self.assertEqual(self.c.value, '456')
        reset()
        self.main('multiple_options_as_type --thingy=789')
        self.assertEqual(self.c.value, '789')
        reset()
        self.main('multiple_options_as_type -y=101112')
        self.assertEqual(self.c.value, '101112')

        reset()
        self.main('multiple_options_as_type')
        self.assertEqual(self.c.value, 'unset')

    def test_double_dash(self):
        """
        command -- -a -b -c

        -- means "all remaining arguments are positional parameters"
        """
        def double_dash(self, a, b='optional', c='empty', *, q, debug):
            self.a = a
            self.b = b
            self.c = c
            self.q = q
            self.debug = debug

        def reset():
            self.c.a = self.c.b = self.c.c = self.c.q = self.c.debug = None
            self.add(double_dash)

        reset()
        self.main('double_dash 1 2 3')
        self.assertEqual(self.c.a, '1')
        self.assertEqual(self.c.b, '2')
        self.assertEqual(self.c.c, '3')
        self.assertEqual(self.c.q, False)
        self.assertEqual(self.c.debug, False)

        reset()
        self.main('double_dash -q --debug 1')
        self.assertEqual(self.c.a, '1')
        self.assertEqual(self.c.b, 'optional')
        self.assertEqual(self.c.c, 'empty')
        self.assertEqual(self.c.q, True)
        self.assertEqual(self.c.debug, True)

        reset()
        self.main('double_dash -- -q --debug 1')
        self.assertEqual(self.c.a, '-q')
        self.assertEqual(self.c.b, '--debug')
        self.assertEqual(self.c.c, '1')
        self.assertEqual(self.c.q, False)
        self.assertEqual(self.c.debug, False)

    def test_mixing_positional_arguments_and_options(self):
        def mpaao(self, a:{int}, *, debug, quiet):
            self.a = a
            self.debug = debug
            self.quiet = quiet
        def reset():
            self.c.a = self.c.debug = self.c.quiet = None
            self.add(mpaao)

        reset()
        self.main('mpaao --debug 1')
        self.assertEqual(self.c.a, 1)
        self.assertEqual(self.c.debug, True)
        self.assertEqual(self.c.quiet, False)

        reset()
        self.main('mpaao 2 --debug')
        self.assertEqual(self.c.a, 2)
        self.assertEqual(self.c.debug, True)
        self.assertEqual(self.c.quiet, False)

        reset()
        self.main('mpaao --quiet 3 --debug')
        self.assertEqual(self.c.a, 3)
        self.assertEqual(self.c.debug, True)
        self.assertEqual(self.c.quiet, True)

        reset()
        self.main('mpaao 4 --quiet')
        self.assertEqual(self.c.a, 4)
        self.assertEqual(self.c.debug, False)
        self.assertEqual(self.c.quiet, True)

    def test_star_args(self):
        def star_args(self, a:{int}, *args):
            self.a = a
            self.args = args

        def reset():
            self.c.a = self.c.args = None
            self.add(star_args)

        reset()
        self.main('star_args 4')
        self.assertEqual(self.c.a, 4)
        self.assertEqual(self.c.args, ())

        reset()
        self.main('star_args 5 a b c')
        self.assertEqual(self.c.a, 5)
        self.assertEqual(self.c.args, ('a', 'b', 'c'))

        def star_args_2(self, a:{int}, *args:{int}):
            self.a = a
            self.args = args
        self.add(star_args_2)

        self.main('star_args_2 5 6 7 8 9')
        self.assertEqual(self.c.a, 5)
        self.assertEqual(self.c.args, (6, 7, 8, 9))

    def test_multiple_option(self):
        self.dump = None
        def command(self, *, dump:{'-d *', int}):
            self.dump = dump
        self.add(command)

        self.main('command -d 5 -d 6 -d 7')
        self.assertEqual(self.c.dump, [5, 6, 7])

    def test_global_options(self):
        def glo(*, thingy:{str}='', silent:{'-s'}=False):
            self.c.thingy = thingy
            self.c.silent = silent

        def command1(self, a:{int}, b:{int}=1):
            self.a = a
            self.b = b

        def command2(self, a, b=''):
            self.a = a
            self.b = b

        def reset():
            self.c.a = self.c.b = self.c.thingy = self.c.silent = None
            self.dp.global_handler = glo
            self.add(command1)
            self.add(command2)

        reset()
        self.main('command1 3 5')
        self.assertEqual(self.c.a, 3)
        self.assertEqual(self.c.b, 5)

        reset()
        self.main('command2 a b')
        self.assertEqual(self.c.a, 'a')
        self.assertEqual(self.c.b, 'b')

        reset()
        self.main('--silent command1 99')
        self.assertEqual(self.c.a, 99)
        self.assertEqual(self.c.b, 1)
        self.assertEqual(self.c.silent, True)

        reset()
        self.main('--thingy=abc command2 xyz')
        self.assertEqual(self.c.a, 'xyz')
        self.assertEqual(self.c.b, '')
        self.assertEqual(self.c.silent, False)
        self.assertEqual(self.c.thingy, 'abc')




def eval_or_str(s):
    try:
        return eval(s, {}, {})
    except (NameError, SyntaxError):
        return s

if __name__ == "__main__":
    sys.exit("no module functionality yet!")
