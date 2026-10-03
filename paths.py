# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

import bpy
import glob
import os
import platform
import shutil
import subprocess
from subprocess import Popen
from bpy.types import Operator
from . import ui, utils, gob_import


def gob_init_os_paths():   
    isMacOS = False
    useZSH = False
    if platform.system() == 'Windows':  
        print("GoB Found System: ", platform.system())
        isMacOS = False
        PATH_GOZ = os.path.join(os.environ['PUBLIC'] , "Pixologic")

    elif platform.system() == 'Darwin': #osx
        print("GoB Found System: ", platform.system())

        # with macOS Catalina (10.15) apple switched from bash to zsh as default shell
        if platform.mac_ver()[0] < str(10.15):
            print("use bash")
            useZSH = False
        else: 
            print("use zsh")
            useZSH = True

        isMacOS = True        
        #print(os.path.isfile("/Users/Shared/Pixologic/GoZBrush/GoZBrushFromApp.app/Contents/MacOS/GoZBrushFromApp"))
        PATH_GOZ = os.path.join("/", "Users", "Shared", "Pixologic")
    else:
        print("GoB Unkonwn System: ", platform.system())
        PATH_GOZ = False ## NOTE: GOZ seems to be missing, reinstall from zbrush
    
    PATH_GOB =  os.path.abspath(os.path.dirname(__file__))
    PATH_BLENDER = os.path.join(bpy.app.binary_path)    
    PATH_OBJLIST = os.path.join(PATH_GOZ, "GoZBrush", "GoZ_ObjectList.txt")
    PATH_CONFIG = os.path.join(PATH_GOZ, "GoZBrush", "GoZ_Config.txt") 
    PATH_SCRIPT = os.path.join(PATH_GOB, "ZScripts", "GoB_Import.txt")
    PATH_VARS = os.path.join(PATH_GOZ, "GoZProjects", "Default", "GoB_variables.zvr")  

    return isMacOS, PATH_GOB, PATH_BLENDER, PATH_GOZ, PATH_OBJLIST, PATH_CONFIG, PATH_SCRIPT, PATH_VARS


#create GoB paths when loading the addon
isMacOS, PATH_GOB, PATH_BLENDER, PATH_GOZ, PATH_OBJLIST, PATH_CONFIG, PATH_SCRIPT, PATH_VARS = gob_init_os_paths()
DEFAULT_PATH_GOZ = PATH_GOZ
#print("GoZ Paths: ", gob_init_os_paths())


def set_goz_path(goz_path):
    """Set the GoZ root and refresh every path derived from it."""
    global PATH_GOZ, PATH_OBJLIST, PATH_CONFIG, PATH_VARS

    PATH_GOZ = os.fspath(goz_path)
    PATH_OBJLIST = os.path.join(PATH_GOZ, "GoZBrush", "GoZ_ObjectList.txt")
    PATH_CONFIG = os.path.join(PATH_GOZ, "GoZBrush", "GoZ_Config.txt")
    PATH_VARS = os.path.join(
        PATH_GOZ, "GoZProjects", "Default", "GoB_variables.zvr"
    )


def set_goz_path_from_preferences(preferences=None):
    """Apply the active platform's configured or default GoZ root."""
    preferences = preferences or utils.prefs()
    if preferences.custom_pixologoc_path:
        set_goz_path(utils.get_pixologic_path(preferences))
    else:
        set_goz_path(DEFAULT_PATH_GOZ)


def join_goz_path(directory, *names):
    """Join a GoZ path with names, in GoZ's forward-slash form.

    The project path is a user-set preference whose default carries a trailing
    slash. Every consumer used to rely on that with plain string concatenation,
    so a project path typed without the trailing separator -- which the file
    browser happily produces -- wrote the .ztn markers, the object list entries
    and the exported textures next to the intended folder instead of inside it.

    Forward slashes are kept deliberately: the path is handed to ZBrush both
    inside the .GoZ file and through GoB_variables.zvr, and every other path the
    add-on writes uses that form. Windows accepts forward slashes in file APIs,
    so the same string works for opening the file here.
    """
    joined = os.path.join(os.fspath(directory), *names)
    return joined.replace("\\", "/")


def goz_root_from_preferences(preferences=None):
    """Return the active platform's configured or default GoZ root.

    Unlike set_goz_path_from_preferences this has no side effect, so callers
    that just need the path do not have to read the module globals or worry
    about another caller having changed them.
    """
    preferences = preferences or utils.prefs()
    if preferences.custom_pixologoc_path:
        return utils.get_pixologic_path(preferences)
    return DEFAULT_PATH_GOZ


def find_zbrush_user_asset_roots():
    """Return Maxon ZBrush 2026+ user-asset folders (Roaming/AppSupport)."""
    roots = []
    env_dir = os.environ.get("ZBRUSH_USER_ASSETS_DIR")
    if env_dir and os.path.isdir(env_dir):
        roots.append(os.path.abspath(env_dir))

    if platform.system() == "Windows":
        appdata = os.environ.get("APPDATA")
        if appdata:
            maxon_root = os.path.join(appdata, "Maxon")
            roots.extend(glob.glob(os.path.join(maxon_root, "ZBrush_*")))
            roots.extend(glob.glob(os.path.join(maxon_root, "Maxon ZBrush *")))
    elif platform.system() == "Darwin":
        home = os.path.expanduser("~")
        for parent in (
            os.path.join(home, "Library", "Application Support", "Maxon"),
            os.path.join(home, "Library", "Preferences", "Maxon"),
        ):
            roots.extend(glob.glob(os.path.join(parent, "ZBrush_*")))
            roots.extend(glob.glob(os.path.join(parent, "Maxon ZBrush *")))

    unique_roots = []
    seen = set()
    for root in roots:
        normalized = os.path.abspath(root)
        if normalized not in seen and os.path.isdir(normalized):
            seen.add(normalized)
            unique_roots.append(normalized)
    return unique_roots


def find_zbrush_plugs64_dirs(zbrush_exec=None):
    """Writable ZPlugs64 folders where ZBrush 2026.1+ resolves plugin data."""
    dirs = []
    for root in find_zbrush_user_asset_roots():
        zstartup = os.path.join(root, "ZStartup")
        plugs = os.path.join(zstartup, "ZPlugs64")
        if os.path.isdir(plugs):
            dirs.append(plugs)
        elif os.path.isdir(zstartup):
            try:
                os.makedirs(plugs, exist_ok=True)
                dirs.append(plugs)
            except OSError as exc:
                print("GoB: could not create", plugs, exc)
    return dirs


def deploy_zfileutils(zbrush_exec=None):
    """Copy ZFileUtils next to ZBrush 2026 user plugins so the import script can load it."""
    src = os.path.join(PATH_GOB, "ZScripts", "MyPluginData")
    if not os.path.isdir(src):
        print("GoB: MyPluginData folder is missing:", src)
        return False

    deployed = False
    for plugs in find_zbrush_plugs64_dirs(zbrush_exec):
        dest = os.path.join(plugs, "MyPluginData")
        try:
            os.makedirs(dest, exist_ok=True)
            for name in os.listdir(src):
                source_file = os.path.join(src, name)
                if os.path.isfile(source_file):
                    shutil.copy2(source_file, os.path.join(dest, name))
            print("GoB: deployed ZFileUtils to", dest)
            deployed = True
        except OSError as exc:
            print("GoB: could not deploy ZFileUtils to", dest, exc)
    return deployed


def get_launch_script():
    """Prefer the editable zscript so ZBrush 2026.2+ picks up the import fixes."""
    txt_script = os.path.join(PATH_GOB, "ZScripts", "GoB_Import.txt")
    zsc_script = os.path.join(PATH_GOB, "ZScripts", "GoB_Import.zsc")
    if os.path.isfile(txt_script):
        return txt_script
    return zsc_script


def find_goz_from_app_helper(goz_root=None):
    """Return Pixologic's "model from an application" helper, if installed.

    Every GoZ-enabled application ships its exports by writing the handshake
    files and then running this program, which makes the ZBrush that is
    *already open* import them and starts one only when there is none. It reads
    GoZBrush/GoZ_Application.txt to know which application is exporting.
    """
    root = goz_root or PATH_GOZ
    if not root:
        return None
    for candidate in (
        os.path.join(root, "GoZBrush", "GoZBrushFromApp.exe"),
        os.path.join(root, "GoZBrush", "GoZBrushFromApp.app"),
        os.path.join(root, "GoZBrush", "GoZBrushFromApp"),
    ):
        if os.path.exists(candidate):
            return candidate
    return None


def zbrush_is_running():
    """True when a ZBrush process is already up, so it can be handed the file.

    Detection failure is reported as False on purpose: the caller then falls
    back to launching ZBrush with the import script, which is the behaviour
    that works from a cold start.
    """
    try:
        if platform.system() == "Windows":
            completed = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq ZBrush.exe", "/NH"],
                capture_output=True, text=True, timeout=20,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            return "zbrush.exe" in (completed.stdout or "").lower()
        return (
            subprocess.run(
                ["pgrep", "-x", "ZBrush"], capture_output=True, timeout=20
            ).returncode
            == 0
        )
    except Exception as error:  # pragma: no cover - environment dependent
        print(f"GoB: could not check for a running ZBrush: {error}")
        return False


def goz_object_path_file(goz_root=None):
    """The path ZBrush's own import script reads for a single object."""
    root = goz_root or PATH_GOZ
    return os.path.join(root, "GoZBrush", "GoZ_ObjectPath.txt")


def find_zbrush(self, context, isMacOS):
    #get the highest version of zbrush and use it as default zbrush to send to
    self.is_found = False
    zbrush_exec = utils.get_zbrush_exec()
    if zbrush_exec:
        #OSX .app files are considered packages and cant be recognized with path.isfile and needs a special condition
        if isMacOS:
            if os.path.isdir(zbrush_exec) and 'zbrush.app' in str.lower(zbrush_exec):
                self.is_found = True   

        else: #is PC
            if os.path.isfile(zbrush_exec):  #validate if working file here
                #check if path contains zbrush, that should identify a zbrush executable
                if 'zbrush.exe' in str.lower(zbrush_exec):
                    self.is_found = True

            elif os.path.isdir(zbrush_exec): #search for zbrush files in this folder and its subfolders
                for folder in os.listdir(zbrush_exec):
                    if "zbrush" in str.lower(folder):     #search for content inside folder that contains zbrush
                        #search subfolders for executables
                        if os.path.isdir(os.path.join(zbrush_exec, folder)):
                            i,zfolder = utils.max_list_value(os.listdir(zbrush_exec))
                            for file in os.listdir(os.path.join(zbrush_exec, zfolder)):
                                if ('zbrush.exe' in str.lower(file) in str.lower(file)):
                                    utils.set_zbrush_exec(os.path.join(zbrush_exec, zfolder, file))
                                    self.is_found = True

                        #find executable
                        if os.path.isfile(os.path.join(zbrush_exec,folder)) and ('zbrush.exe' in str.lower(folder) in str.lower(folder)):
                            utils.set_zbrush_exec(os.path.join(zbrush_exec, folder))
                            self.is_found = True

    if not self.is_found:  # try the default location when the configured path is empty or invalid
        #look for zbrush in default installation path 
        if isMacOS:
            folder_List = []                 
            filepath = os.path.join(os.sep, "Applications")
            if os.path.isdir(filepath):
                [folder_List.append(i) for i in os.listdir(filepath) if 'zbrush' in str.lower(i)]
                if folder_List:
                    i, zfolder = utils.max_list_value(folder_List)
                    zbrush_folder = os.path.join(filepath, zfolder)
                    if zfolder.lower().endswith("zbrush.app"):
                        zbrush_exec_path = zbrush_folder
                    else:
                        zbrush_exec_path = os.path.join(zbrush_folder, "ZBrush.app")
                    if os.path.isdir(zbrush_exec_path):
                        utils.set_zbrush_exec(zbrush_exec_path)
                        ui.ShowReport(self, [zbrush_exec_path], "GoB: Zbrush default installation found", 'COLORSET_03_VEC')
                        self.is_found = True
        else:              
            # Determine the base paths based on the preference setting
            if utils.prefs().use_pixologic_path:
                default_paths = [os.path.join("C:\\", "Program Files", "Pixologic")]
            else:
                default_paths = [os.path.join("C:\\", "Program Files")]

            # Check if the default paths exist and search for ZBrush folders
            for base_path in default_paths:
                if not os.path.isdir(base_path):
                    continue
                        
                # Check for ZBrush folders in the base path
                folder_list = [folder for folder in os.listdir(base_path) if 'zbrush' in str.lower(folder)]
                if not folder_list:
                    continue

                i, zfolder = utils.max_list_value(folder_list)
                zbrush_exec_path = os.path.join(base_path, zfolder, "ZBrush.exe")
                if os.path.isfile(zbrush_exec_path):
                    utils.set_zbrush_exec(zbrush_exec_path)
                    ui.ShowReport(self, [zbrush_exec_path], f"GoB: {zfolder} default installation found", 'COLORSET_03_VEC')
                    self.is_found = True
                    break

    if not self.is_found:
        print('GoB: Zbrush executable not found')

    return self.is_found



def is_file_empty(file_path):
    """ Check if file is empty by confirming if its size is 0 bytes"""
    return os.path.exists(file_path) and os.stat(file_path).st_size == 0



class GoB_OT_GoZ_Installer(Operator):
    ''' Run the Pixologic GoZ installer 
        /Troubleshoot Help/GoZ_for_ZBrush_Installer'''
    bl_idname = "gob.install_goz" 
    bl_label = "Run GoZ Installer"

    def execute(self, context):
        """Install GoZ for Windows""" 
        if path_exists := find_zbrush(self, context, isMacOS):
            path = os.path.dirname(utils.get_zbrush_exec())
            if isMacOS:
                GOZ_INSTALLER = os.path.join(path, "Troubleshoot Help", "GoZ_for_ZBrush_Installer_OSX.app")
                Popen(['open', '-a', GOZ_INSTALLER])  
            else:    
                GOZ_INSTALLER = os.path.join(path, "Troubleshoot Help", "GoZ_for_ZBrush_Installer_WIN.exe")
                Popen([GOZ_INSTALLER], shell=True)
        else:
            bpy.ops.gob.search_zbrush('INVOKE_DEFAULT')
        return {'FINISHED'}
