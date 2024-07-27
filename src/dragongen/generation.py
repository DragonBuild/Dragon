#!/usr/bin/env python3

"""

DragonGen.py

(c) 2020 cynder
Please refer to the LICENSE file included with this project regarding the usage of code herein.

this file looks moderately sane thanks to wonderful work by @l0renzo

any remaining or new travesties are my own fabrications.

"""

import traceback, platform, yaml, termios, tty
from collections import namedtuple
from datetime import datetime
from typing import TextIO
from .variable_types import ProjectVars
from .util import *
from .toolchain import Toolchain
from buildgen.generator import BuildFileGenerator
from shared.util import dbstate, dbwarn, dberror

# Rules and defaults
_LAZY_RULES_DOT_YML: dict = None
_LAZY_DEFAULTS_DOT_YML: dict = None

_IS_THEOS_MAKEFILE_ = False

_RELEASE_BUILD = False

# These are used like so:
# a_build_object = Build("output files here", "rule name here", "input files here")
# outputs = a_build_object.outputs

Build = namedtuple('Build', ['outputs', 'rule', 'inputs'])
Comment = namedtuple('Comment', ['fstring'])
Rule = namedtuple('Rule', ['name', 'description', 'command'])
Var = namedtuple('Var', ['key'])
Default = namedtuple('Default', ['targets'])
___ = object()  # Newline


# Get rule by name from rules.yml
def get_generic_rule(name: str) -> Rule:
    return Rule(name, rules(name, 'desc'), rules(name, 'cmd'))


class Generator(object):

    def __init__(self, config: dict, module_name: str, target_platform: str):
        self.config: dict = config
        self.module_name: str = module_name
        self.target_platform: str = target_platform
        self.project_variables: ProjectVars = None

    def write_output_file(self, stream: TextIO):
        """
        Evaluate outline with variables and write ninja file to given IO stream

        Keyword arguments:
        stream -- IO stream to which the ninja data should be writen
        """

        # Compute project variables
        self.project_variables: ProjectVars = ProjectVars(
            self.generate_vars(self.config[self.module_name], self.target_platform))

        # Generate the outline
        outline = self.generate_ninja_outline()

        # Set up the output file generator (buildgen)
        gen = BuildFileGenerator(stream)

        # Iterate through the outline and write it with buildgen to the ninja/makefile
        for item in outline:
            if item == ___:
                gen.newline()
                continue
            if isinstance(item, Comment):
                gen.comment(item.fstring)
                continue
            if isinstance(item, Var):
                gen.variable(item.key, str(self.project_variables[item.key]))
                continue
            if isinstance(item, Rule):
                gen.rule(item.name,
                         description=item.description,
                         command=item.command)
                continue
            if isinstance(item, Build):
                gen.build(item.outputs, item.rule, item.inputs)
                continue
            if isinstance(item, Default):
                gen.default(['$build_target_file'])

    def generate_vars(self, module_variables: dict, target: str) -> dict:
        """
        Generate ProjectVars object for a project

        Keyword arguments:
        module_variables -- dict of explicitly set variables for this project
        target -- target platform

        Raises: KeyError
        """

        if 'for' in module_variables:
            target = module_variables['for']

        # Load in internal defaults
        # These are ones that really probably shouldn't be touched often as they
        # serve to slap together all the variables we *do* touch
        project_dict: dict = get_default_section_dict('InternalDefaults')

        if _IS_THEOS_MAKEFILE_:
            project_dict.update({
                'theosshim': '-include$$DRAGON_ROOT_DIR/include/PrefixShim.h -w'
            })

        # Setup with default vars
        project_dict.update(get_default_section_dict('Defaults'))  # Universal
        try:
            # Apply Type Variables
            project_dict.update(get_default_section_dict('Types',
                                                         module_variables['type'], 'variables'))  # Type-based
        except KeyError as ex:
            try:
                # Cast type to lowercase and try again
                project_dict.update(get_default_section_dict('Types',
                                                             module_variables['type'].lower(), 'variables'))
            except KeyError:
                # They either didn't include a type variable, or they misspelled the
                # type they used.
                raise ex

        # Load rootless config
        if int(os.environ['rootless']) == 1:
            project_dict.update(get_default_section_dict('Rootless'))

        # Apply the set of variables the user included on this module
        project_dict.update(module_variables)
        # Apply the module name
        project_dict['name'] = self.module_name

        # We allow the user to create their own Target and all sections
        # Iterate through defaults.yml, the module's specific variables, and the root of
        # the DragonMake
        for source in get_default_section_dict(), module_variables, self.config:
            if 'all' in source:
                project_dict.update(source['all'])
            if 'Targets' in source and target in source['Targets'] and 'all' in source['Targets'][target]:
                project_dict.update(source['Targets'][target]['all'])

        project_dict.update(module_variables)

        # MACHINE checks
        for d, i in enumerate(project_dict['archs']):
            if 'MACHINE' in i:
                project_dict['archs'][d] = platform.machine()

        if 'triple' in project_dict and project_dict['triple'] != '':
            project_dict['triple'] = '-target ' + os.popen('clang -print-target-triple').read().strip() \
                if 'MACHINE' in project_dict['triple'] else '-target ' + project_dict['triple']

        # A few variables that need to be renamed
        NINJA_KEYS = {
            'location': 'install_location',
            'btarg': 'targ',
            'header_includes': 'include',
            'typeldflags': 'ldflags',
            'lopt': 'lopts'
        }
        # Rename them
        project_dict.update({key: project_dict[NINJA_KEYS[key]]
                             for key in NINJA_KEYS
                             if NINJA_KEYS[key] in project_dict})

        project_dict['lowername'] = str(project_dict['name']).lower()

        # Apply framework/lib search and additional search dirs
        project_dict['fwSearch'] = project_dict['fw_dirs'] \
                                   + (project_dict['additional_fw_dirs']
                                      if project_dict['additional_fw_dirs']
                                      else [])
        project_dict['libSearch'] = project_dict['lib_dirs'] \
                                    + (project_dict['additional_lib_dirs']
                                       if project_dict['additional_lib_dirs']
                                       else [])

        # Specify toolchain paths
        use_objcs = 'objcs' in project_dict

        sys = platform.system()
        if sys == 'Darwin':
            plat = platform.platform()
            if 'iP' in plat:
                toolchain = Toolchain()
            else:
                toolchain = Toolchain.locate_macos_toolchain(use_objcs)
        # elif WINDOWS IS NOT A REAL OPERATING SYSTEM ::::)
        elif sys == 'Linux':
            toolchain = Toolchain.locate_linux_toolchain(use_objcs)
        else:
            toolchain = Toolchain()

        if toolchain is None:
            dberror("Dragon Gen", "Could not locate any usable toolchain or even determine the existence of clang.")
            dberror("Dragon Gen", "If you're on macOS, install XCode and the Command Line Tools package")
            dberror("Dragon Gen", "If you're on linux, install L1ghtmann's iOS toolchain to ~/.dragon/toolchain")
            dberror("Dragon Gen", "You can also add the key 'objcs': True to your DragonMake module to have dragon automatically"
                    "install its internal toolchain, however note this isn't really reccomended for non-objcs "
                    "projects")
            exit(64)

        if 'cc' not in project_dict:
            project_dict['cc'] = toolchain.clang
        if 'cxx' not in project_dict:
            project_dict['cxx'] = toolchain.clangpp
        if 'lipo' not in project_dict:
            project_dict['lipo'] = toolchain.lipo
        if 'dsym' not in project_dict:
            project_dict['dsym'] = toolchain.dsym
        if 'ld' not in project_dict:
            project_dict['ld'] = toolchain.ld
        if 'codesign' not in project_dict:
            project_dict['codesign'] = toolchain.codesign
        if 'strip' not in project_dict:
            project_dict['strip'] = toolchain.strip

        # TODO: lazy hack
        if 'cxxflags' in project_dict:
            project_dict['cxx'] = project_dict['cxx'] + ' ' + project_dict['cxxflags']

        # TODO: move this to arglist maybe
        if project_dict['sysroot']:
            project_dict['sysroot'] = '-isysroot' + project_dict['sysroot']

        if 'name_override' in project_dict:
            project_dict['name'] = project_dict['name_override']

        log.debug("project_dict after processing through generate_vars:" + log.format(project_dict))

        return project_dict

    def rules_and_build_statements(self) -> (list, list):
        """
        Generate build statements and rules for a given variable set.

        Returns rule_list, build_state as extensions for an outline
        """

        # Trivial project types
        if self.project_variables['type'] == 'resource-bundle':
            return [
                get_generic_rule('bundle'),
                get_generic_rule('stage'),
            ], [
                Build('bundle', 'bundle', 'build.ninja'),
                Build('stage', 'stage', 'build.ninja'),
            ]
        if self.project_variables['type'] == 'stage':
            return [
                get_generic_rule('stage'),
            ], [
                Build('stage', 'stage', 'build.ninja'),
            ]

        # Only load rules we need
        # { file_type : corresponding_rule_name }
        FILE_RULES = {
            'c_files': 'c',
            'cxx_files': 'cxx',
            'dlists': None,
            'files': None,
            'logos_files': None,
            'objc_files': 'objc',
            'objcxx_files': 'objcxx',
            'plists': None,
            'swift_files': 'swift',
        }

        build_state = []
        rule_list = []
        used_rules = {'sign', 'stage', 'lipo'}
        if _RELEASE_BUILD:
            used_rules.add('strip')
        else:
            used_rules.add('debug')
        subdir: str = self.project_variables['dir'] + '/'
        filedict = classify({key: self.project_variables[key] for key in FILE_RULES})
        linker_conds = set()

        # switch to cxx for pp files (auto-links ++ stdlib)
        if any("cxx" in ftype for ftype in filedict):
            self.project_variables["ld"] = self.project_variables["cxx"]

        # Deal with logos preprocessing
        if 'logos_files' in filedict:
            for f in standardize_file_list(subdir, filedict['logos_files']):
                used_rules.add('logos')
                linker_conds.add('-lobjc')

                name, ext = os.path.split(f)[1], os.path.splitext(f)[1]
                if ext == '.x':
                    build_state.append(Build(f'$builddir/logos/{name}.m', 'logos', f))
                    filedict.setdefault('objc_files', [])  # Create a list here if it doens't exist
                    filedict['objc_files'].append(f'$builddir/logos/{name}.m')
                elif ext == '.xm':
                    build_state.append(Build(f'$builddir/logos/{name}.mm', 'logos', f))
                    filedict.setdefault('objcxx_files', [])
                    filedict['objcxx_files'].append(f'$builddir/logos/{name}.mm')
                    # switch to cxx for pp files (auto-links ++ stdlib)
                    self.project_variables["ld"] = self.project_variables["cxx"]

        # Deal with compilation
        archs = self.project_variables['archs']
        if any(x in ['armv6', 'armv7', 'armv7s'] for x in archs):
            # https://twitter.com/saurik/status/654198997024796672
            # iOS 9+ 32-bit pagesize on 64-bit CPUs changed to 16384 bytes from 4096
            linker_conds.add("-Xlinker -segalign -Xlinker 4000")

        for a in archs:
            arch_specific_object_files = []

            for ftype in (f for f in FILE_RULES if FILE_RULES[f] is not None and f in filedict):
                ruleid = f'{FILE_RULES[ftype]}'
                for f in standardize_file_list(subdir, filedict[ftype]):
                    # if file type has a rule
                    if ruleid is not None:
                        name = os.path.split(f)[1]
                        rulename = ruleid + f'{a}'
                        # if rule is not already defined
                        if not any(r.name == rulename for r in rule_list):
                            rule_list.append(Rule(rulename, rules(ruleid, 'desc', replace={'{arch}' : f'{a}'}), rules(ruleid, 'cmd', replace={'{arch}' : f'{a}'})))
                        arch_specific_object_files.append(f'$builddir/{a}/{name}.o')
                        build_state.append(Build(f'$builddir/{a}/{name}.o', rulename, f))

                    LINKER_FLAGS = {  # Don't link objc if not needed
                        'objc': ['-lobjc'],
                        'objcxx': ['-lobjc'],
                    }

                    if ftype in LINKER_FLAGS:
                        for flag in LINKER_FLAGS[ftype]:
                            linker_conds.add(flag)

            # Linker rules and build statements
            cmd = rules('link', 'cmd', replace={'{arch}' : f'{a}'}) + ' ' + ' '.join(linker_conds)
            if self.project_variables['type'] == 'static':
                cmd = rules('archive', 'cmd') + ' ' + ' '.join(linker_conds)
            rule_list.append(Rule(f'link{a}', rules('link', 'desc', replace={'{arch}' : f'{a}'}), cmd))
            build_state.append(Build(f'$builddir/$name.{a}',
                                        f'link{a}',
                                        arch_specific_object_files))

        build_state.extend([
            # lipo if needed, else use a copy rule to rename it to what the next rule expects
            # the copy rule could be optimized out, but its probably more developmentally clear
            # to have it there anyways /shrug
            Build('$internalsymtarget',
                  'lipo' if len(self.project_variables['archs']) > 1 else 'copy',
                  [f'$builddir/$name.{a}' for a in self.project_variables['archs']]),
            # expecting a .unsigned
            Build('$internalsigntarget', 'copy', '$internalsymtarget'),
            # Debug symbols
            # debug & strip run on a file as-is -- Ninja will skip that sort of build rule
            # as there's nothing to track. we use a 'phonytarget' to tie the debug/strip step
            # to the sign step, which is always run, via an implicit, secondary dependency (| dep)
            Build('phonytarget', 'strip' if _RELEASE_BUILD else 'debug', '$internalsigntarget'),
            # Codesign
            Build('$build_target_file', 'dummy' if self.project_variables['type'] == 'static' else 'sign', '$internalsigntarget|phonytarget'),
            # Stage commands (these are actually ran at a different point in the 'runner')
            Build('stage', 'stage', 'build.ninja'),
        ])

        # Fix used_rules, TODO: maybe this could be optimized elsewhere?
        if len(self.project_variables['archs']) <= 1:
            used_rules.remove("lipo")

        used_rules.add("copy")

        if self.project_variables['type'] == 'static':
            # use a dummy rule to rename it to what the next rule expects
            # the dummy rule could be optimized out, but its probably more developmentally clear
            # to have it there anyways /shrug
            used_rules.remove("sign")
            used_rules.add("dummy")

        rule_list.extend(get_generic_rule(r) for r in used_rules)

        return rule_list, build_state

    def generate_ninja_outline(self) -> list:
        """
        Generate list of unevaluated build.ninja statements

        Seealso: rules_and_build_statements
        """

        outline = [
            Var('name'),
            Var('lowername'),
            ___,
            Comment(f'Build file for {self.project_variables["name"]}'),
            Comment(f'Generated at {datetime.now().strftime("%D %H:%M:%S")}'),
            ___,
            Var('stagedir'),
            Var('location'),
            Var('dragon_root_dir'),
            Var('sysroot'),
            Var('dragon_data_dir'),
            Var('objdir'),
            Var('signdir'),
            Var('builddir'),
            Var('build_target_file'),
            Var('pwd'),
            Var('resource_dir'),
            Var('toolchain-prefix'),
            ___,
            Var('stage'),
            Var('stage2'),
            ___,
            ___,
            Var('internalsigntarget'),
            Var('internalsymtarget'),
            ___,
            Var('fwSearch'),
            Var('libSearch'),
            Var('modulesinternal'),
            ___,
            Var('cc'),
            Var('codesign'),
            Var('cxx'),
            Var('dsym'),
            Var('ld'),
            Var('lipo'),
            Var('logos'),
            Var('optool'),
            Var('swift'),
            Var('strip'),
            ___,
            Var('targetvers'),
            Var('targetos'),
            Var('triple'),
            ___,
            Var('frameworks'),
            Var('libs'),
            ___,
            Var('prefix'),
            Var('macros'),
            Var('arc'),
            Var('btarg'),
            Var('debug'),
            Var('entfile'),
            Var('entflag'),
            Var('stripflags'),
            Var('optim'),
            Var('warnings'),
            ___,
            Var('cinclude'),
            Var('header_includes'),
            Var('public_headers'),
            ___,
            Var('usrCflags'),  # Unused?
            Var('usrLDflags'),  # Unused?
            ___,
            Var('libflags'),
            Var('lopts'),
            Var('typeldflags'),
            ___,
            Var('cflags'),
            Var('ldflags'),
            Var('lflags'),
            Var('lfflags'),
            Var('swiftflags'),
            ___,
            Var('internalreleaseflags') if _RELEASE_BUILD else Var('internaldbgflags'),
            Var('releaseflags') if _RELEASE_BUILD else Var('dbgflags'),
            ___,
            Var('rootless_prefix'),
            Var('rpathflags'),
            ___,
            Var('theosshim'),
            Var('internalcflags'),
            Var('internalldflags'),
            Var('internallflags'),
            Var('internallfflags'),
            Var('internalswiftflags'),
            ___,
        ]

        rule_list, build_state = self.rules_and_build_statements()

        outline.extend(rule_list)
        outline.append(___)
        outline.extend(build_state)
        outline.append(___)
        outline.append(Default(['$build_target_file']))

        return outline


def replace_placeholders(data, replace):
    if isinstance(data, dict):
        return {k: replace_placeholders(v, replace) for k, v in data.items()}
    elif isinstance(data, list):
        return [replace_placeholders(v, replace) for v in data]
    elif isinstance(data, str):
        for key, value in replace.items():
            data = data.replace(key, value)
    return data


def rules(*key_path: str, replace: dict = None) -> dict:
    """
    Lazy load default rules and return value specified path.

    Raises: FileNotFoundError, KeyError
    """

    global _LAZY_RULES_DOT_YML
    if _LAZY_RULES_DOT_YML is None:
        with open(f'{os.environ["DRAGON_ROOT_DIR"]}/internal/rules.yml') as f:
            _LAZY_RULES_DOT_YML = yaml.safe_load(f)

    key_path = list(key_path)
    ret = _LAZY_RULES_DOT_YML.copy()
    while key_path:
        ret = ret[key_path.pop(0)]
    if replace is not None:
        ret = replace_placeholders(ret, replace)
    return ret


def get_default_section_dict(*key_path: str) -> dict:
    """
    Lazy load defaults.yml and return the requested dictionary from it

    Raises: FileNotFoundError, KeyError
    """

    global _LAZY_DEFAULTS_DOT_YML
    with open(f'{os.environ["DRAGON_ROOT_DIR"]}/internal/defaults.yml') as f:
        _LAZY_DEFAULTS_DOT_YML = yaml.safe_load(f)
    with open(f'{os.environ["DRAGON_ROOT_DIR"]}/internal/targets.yml') as f:
        _LAZY_DEFAULTS_DOT_YML.update(yaml.safe_load(f))
    with open(f'{os.environ["DRAGON_ROOT_DIR"]}/internal/types.yml') as f:
        _LAZY_DEFAULTS_DOT_YML.update(yaml.safe_load(f))

    key_path = list(key_path)
    ret = _LAZY_DEFAULTS_DOT_YML.copy()
    while key_path:
        ret = ret[key_path.pop(0)]
    return ret


def handle(ex: Exception):
    """ Optionally print debug information """

    dberror("Dragon Gen", "Press v for detailed debugging output, any other key to exit.")

    try:
        old_setting = termios.tcgetattr(sys.stdin.fileno())
        tty.setraw(sys.stdin)
        x = sys.stdin.read(1)
        termios.tcsetattr(0, termios.TCSADRAIN, old_setting)
        if str(x).lower() == 'v':
            dberror("Dragon Gen", str(ex))
            dberror("Dragon Gen", ''.join(traceback.format_tb(ex.__traceback__)))
        else:
            dberror("Dragon Gen", "Exiting...")
    except Exception:
        dberror("Dragon Gen", f'tty/termios unavailable: {ex}')
        dberror("Dragon Gen", "Exiting...")

    print('export DRAGONGEN_FAILURE=1')


def main():
    """
    Generate and write build.ninja file from DragonMake or Makefile

    Outline of this method:
        - Pull in the actual raw dict from the DragonMake or Makefile
        - Process raw data if needed via the Makefile or Legacy (bash) interpreters
        - Iterate through the top-level keys in the dictionary
        - Call the Generator class to write to {top-level-key-name}.ninja for each
        - Pass some export commands to the parent bash script via stdout
    """
    META_KEYS = {  # Keys that may be at the root of the DragonMake dict
        'name': 'package_name',
        'icmd': 'install_command',
        'ip': 'DRBIP',
        'postinst': None,
        'preinst': None,
        'postrm': None,
        'prerm': None,
        'port': 'DRBPORT',
        'id': None,
        'mtn': None,
        'author': None,
        'version': None,
        'depends': None,
        'conflicts': None,
        'architecture': None,
        'description': None,
        'section': None,
        'pack': None,
        'package': None,
        'desc': None,
        'all': None,
        'icon': None,
        'depiction': None,
        'sldepiction': None,
        'sileodepiction': None
    }

    exports = {}
    dirs = ''
    projs = ''

    if os.path.exists('DragonMake'):
        with open('DragonMake') as f:
            try:
                config = yaml.safe_load(f)
            except Exception as ex:
                # If the file we tried to load isn't YAML, try running it as a bash script
                if os.system("sh DragonMake 2>/dev/null") == 0:
                    # If that worked, it's the old (OLD) legacy DragonMake format,
                    # which we can easily support via a couple lines of regex
                    config = load_old_format(open('DragonMake'))
                    dbstate("Dragon Gen", "Loading Legacy format DragonMake")
                else:
                    # bad format
                    dberror("Dragon Gen", "Formatting Error in the DragonMake file")
                    dberror("Dragon Gen", "Check YAML syntax or file an issue")
                    dberror("Dragon Gen", 'https://github.com/DragonBuild/dragon')
                    raise ex

    elif os.path.exists('Makefile'):
        from .theos import TheosMakefileProcessor
        config = TheosMakefileProcessor().project
        # config = interpret_theos_makefile(open('Makefile'))
        exports['theos'] = 1
        dbstate("Dragon Gen", "Generating build scripts from Theos Makefile")
        global _IS_THEOS_MAKEFILE_
        _IS_THEOS_MAKEFILE_ = True

    else:
        raise FileNotFoundError

    dbstate("Dragon Gen", "Generating build scripts")
    for key in config:
        if key in META_KEYS:
            continue

        # Hack to run a bash command in the context of DragonGen from a DragonMake file
        if key == 'exports':
            exports.update(config[key])
            continue

        submodule_config = {
            'name': key,
            'dir': '.'
        }
        try:
            submodule_config.update(config[key])
        except ValueError:
            # if i add a key to control.py and don't add it to meta tags here, this happens
            # so maybe find a better way to do that, dpkg is complex and has many fields
            dbwarn("Dragon Gen", "! Warning: Key %s is not a valid module (a dictionary),"
                   " nor is it a known configuration key" % key)
            dbwarn("Dragon Gen", "! This key will be ignored.")
            continue

        default_target = 'ios'
        if os.environ['TARG_SIM'] == '1':
            default_target = 'sim'

        with open(f'{submodule_config["dir"]}/{submodule_config["name"]}.ninja', 'w+') as out:
            try:
                generator = Generator(config, key, default_target)
                dbstate("Dragon Gen", f"Creating build script for {key}")
                generator.write_output_file(out)
            except Exception as ex:
                dberror("Dragon Gen", f'Exception in module "{key}":')
                raise ex

        dirs = dirs + ' ' + submodule_config['dir']
        dirs = dirs.strip()
        if dirs.endswith('.'):
            dirs = '. ' + dirs[:-2]

        projs = projs + ' ' + submodule_config['name']
        projs = projs.strip()

    exports['project_dirs'] = dirs
    exports['project_names'] = projs

    for x in exports:
        print(f'export {x}="{exports[x]}"')


if __name__ == '__main__':
    try:
        if os.environ['DGEN_DEBUG']:
            log.LOG_LEVEL = LogLevel.DEBUG
        if os.environ['RELEASE'] == "1":
            _RELEASE_BUILD = True
        main()
    except FileNotFoundError as exception:
        print('Error: No project files found', file=sys.stderr)

        handle(exception)
        sys.exit(2)
    except KeyError as exception:
        print('KeyError: Missing value in variables array.', file=sys.stderr)
        print(str(exception), file=sys.stderr)

        handle(exception)
        sys.exit(2)
    except IndexError as exception:
        print("IndexError: List index out of range.", file=sys.stderr)
        print(str(exception), file=sys.stderr)

        handle(exception)
        sys.exit(2)
    except Exception as exception:
        print('Error: An undocumented error has been hit', file=sys.stderr)
        print('Please contact a maintainer', file=sys.stderr)

        handle(exception)
        sys.exit(-1)
