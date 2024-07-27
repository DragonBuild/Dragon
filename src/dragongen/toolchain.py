import os
from shared.util import system_with_output

class Toolchain:
    def __init__(self):
        self.clang = "clang"
        self.clangpp = "clang++"
        self.ass = self.clang
        self.ld = self.clang
        self.codesign = "ldid"
        self.dsym = "dsymutil"
        self.plutil = "plutil"
        self.lipo = "lipo"
        self.tapi = "tapi"
        self.strip = "strip"

    @classmethod
    def locate_macos_toolchain(cls, use_objcs: bool):
        tc_dir = ""
        if use_objcs:
            tc_dir = os.environ['DRAGON_ROOT_DIR'] + '/llvm-objcs/bin/'
        else:
            stat, xcrun_out, _ = system_with_output('xcrun -f clang')
            tc_dir = ""

            if stat == 0:
                tc_dir = os.path.dirname(xcrun_out) + '/'
            elif os.path.exists('/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/'):
                tc_dir = '/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/'
            elif system_with_output('command -v clang')[0] == 0:
                pass
            else:
                return None

        tc = cls()
        tc.clang = tc_dir + 'clang'
        tc.clangpp = tc_dir + 'clang++'
        tc.ass = tc.clang
        tc.ld = tc.clang
        tc.dsym = tc_dir + 'dsymutil'
        # FIXME: hardcoded while I wait on a real distribution of llvm-objcs
        if use_objcs:
            tc.lipo = '/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/bin/lipo'
        else:
            tc.lipo = tc_dir + 'lipo'
        tc.tapi = tc_dir + 'tapi'

        return tc

    @classmethod
    def locate_linux_toolchain(cls, use_objcs: bool):
        tc_dir = ""
        if use_objcs:
            tc_dir = os.environ['DRAGON_ROOT_DIR'] + '/llvm-objcs/bin/'
        elif os.path.exists(os.environ['DRAGON_ROOT_DIR'] + '/toolchain/linux/iphone/bin/'):
            tc_dir = os.environ['DRAGON_ROOT_DIR'] + '/toolchain/linux/iphone/bin/'
        elif system_with_output('command -v clang')[0] == 0:
            pass
        else:
            return None

        tc = cls()
        tc.clang = tc_dir + 'clang'
        tc.clangpp = tc_dir + 'clang++'
        tc.ass = tc.clang
        tc.ld = tc.clang
        tc.codesign = tc_dir + 'ldid'
        tc.dsym = tc_dir + 'dsymutil'
        tc.lipo = tc_dir + 'lipo'
        tc.tapi = tc_dir + 'tapi'
        tc.strip = tc_dir + 'strip'

        return tc


