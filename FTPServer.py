import socket
import threading
import os
from datetime import datetime
import re
import utilities
import ntsecuritycon as con
from Encryption_Methods import SSL_TLS_Encryption, TLS_Encryption, SSL_Encryption

# Constants defining server configurations and behavior-----------------------------------------------------------------
HEADER = 256  # Fixed header size for receiving commands
FORMAT = 'utf-8'  # Encoding format for communication
DISCONNECT_MESSAGE = "QUIT"  # Message to indicate disconnection
SERVER_IP = "127.0.0.1"  # Server IP address
CONTROL_PORT = 465  # Control port for communication
DATA_PORT = 2121  # Data port for file transfers
BASE_DIRECTORY = 'D:\\network\\FTP\\FTP\\server-folder'  # Default server storage directory

# check whether a file is transferring or not for Quit command----------------------------------------------------------
IS_TRANSFERRING = {}

# Encryption and FTP mode configurations
ENCRYPTION_MODE = "TLS"  # Default encryption mode
FTP_TYPE = "FTPS"  # FTP type being used

# Ensures the base directory exists, creating it if necessary-----------------------------------------------------------
if not os.path.exists(BASE_DIRECTORY):
    os.makedirs(BASE_DIRECTORY)

# Dictionary to track file locks for concurrency management
file_locks = {}
file_locks_lock = threading.Lock()  # Mutex for safe access to file_locks
operation_timeout = 60  # Timeout for file operations (in seconds)


# Function to get or create a lock for a file---------------------------------------------------------------------------
def get_file_lock(filename):
    """Ensures a unique lock for each file, allowing thread-safe operations."""
    with file_locks_lock:
        if filename not in file_locks:
            file_locks[filename] = threading.Lock()
        return file_locks[filename]


#   User levels for role-based access controls--------------------------------------------------------------------------

LEVEL = {
    '1': 'Super-admin',
    '2': 'Admin',
    '3': 'Promoted user',
    '4': 'Normal'
}
# Registered users with associated roles and credentials----------------------------------------------------------------
VALID_USERS = {
    'user1': {'password': 'password1',
              'level': LEVEL['1']},
    'user2': {'password': 'password2', 'level': LEVEL['2']},
    'user3': {'password': 'password3', 'level': LEVEL['3']},
    'user4': {'password': 'password4', 'level': LEVEL['4']},
}

# Global variables for user states and active threads-------------------------------------------------------------------
ZOMBIE_THREADS = {}

# Commands available for admin and regular users------------------------------------------------------------------------
ADMIN_COMMANDS = """
Supported commands:
  LIST                                           - List directory contents
  RETR <filename>                                - Download a file
  STOR <filepath> <destination_path>             - Upload a file
  DELE <filename>                                - Delete a file
  MKD <dirname>                                  - Create a directory
  RMD <dirname>                                  - Remove a directory
  PWD                                            - Print the current directory
  CWD <dirname>                                  - Change directory
  CDUP                                           - Move to the parent directory
  CHANGELEVEL <username> <new_level>             - Change the level of a user
  SETACL <file_path> <username> <permission>     - Modify a file permissions
  QUIT                                           - Disconnect from the server\n
"""
# -----------------------------------------------------------------------------------------------------------------------
USER_COMMANDS = """
Supported commands:
  LIST                                           - List directory contents
  RETR <filename>                                - Download a file
  STOR <filepath> <destination_path>             - Upload a file
  DELE <filename>                                - Delete a file
  MKD <dirname>                                  - Create a directory
  RMD <dirname>                                  - Remove a directory
  PWD                                            - Print the current directory
  CWD <dirname>                                  - Change directory
  CDUP                                           - Move to the parent directory
  QUIT                                           - Disconnect from the server\n
"""

# Map of permission types for files-------------------------------------------------------------------------------------
PERMISSIONS_MAP = {
    "Read": con.FILE_GENERIC_READ,
    "Write": con.FILE_GENERIC_WRITE,
    "Full": con.FILE_ALL_ACCESS,
    "Delete": con.DELETE,
    "Create File": con.FILE_ADD_FILE,
    "Create Subdirectory": con.FILE_ADD_SUBDIRECTORY
}


# Permission management functions---------------------------------------------------------------------------------------


def set_permissions_windows(file_name, username, permission):
    """Sets or updates file permissions for a user."""
    try:
        if username not in VALID_USERS and username != "Everyone":
            return False

        permissions = PERMISSIONS_MAP.get(permission)
        if permissions is None:
            return False

        file_permissions = {}
        if os.path.exists(file_name + ".perm"):
            with open(file_name + ".perm", "r") as f:
                file_permissions = eval(f.read())

        file_permissions[username] = permissions

        with open(file_name + ".perm", "w") as f:
            f.write(str(file_permissions))

        return True

    except Exception as e:
        return False


# ----------------------------------------------------------------------------------------------------------------------


def check_permission(file_perm, user_state, permission):
    """Checks if a user has the specified permission on a file."""
    try:
        if user_state['username'] not in VALID_USERS:
            return False

        permissions = PERMISSIONS_MAP.get(permission)
        if permissions is None:
            return False

        if not os.path.exists(file_perm + ".perm"):
            return False

        with open(file_perm + ".perm", "r") as f:
            file_permissions = eval(f.read())

        permissions_list = file_permissions.get(user_state['username']) or file_permissions.get('Everyone')
        if permissions_list == permissions or permissions_list == PERMISSIONS_MAP.get('Full'):
            return True
        else:
            return False

    except Exception as e:
        return False


# ----------------------------------------------------------------------------------------------------------------------

def get_permissions(file_name, client_socket):
    """Retrieves and formats file permissions for output."""
    try:
        if not os.path.exists(file_name + ".perm"):
            return

        with open(file_name + ".perm", "r") as f:
            file_permissions = eval(f.read())

        result = []
        for user, perm in file_permissions.items():
            user_perms = [k for k, v in PERMISSIONS_MAP.items() if v & perm]
            result.append(f"{user}: {', '.join(user_perms)}")

        if result:
            return ','.join(result) + '\n'
        else:
            return

    except Exception as e:
        client_socket.sendall(f"450 Error retrieving permissions: {e}\n".encode())


#Command handlers for various FTP operations---------------------------------------------------------------------------
def handle_help(user_state, client_socket):
    """Displays a list of available commands based on user role."""
    level = user_state['level']
    commands = ADMIN_COMMANDS if level == LEVEL['1'] or level == LEVEL['2'] else USER_COMMANDS

    client_socket.sendall(f"{commands}\n".encode(FORMAT))


#-----------------------------------------------------------------------------------------------------------------------
def handle_setacl(command_parts, client_socket, user_state):
    """
    Handles the SETACL command to modify file permissions.
    Usage: SETACL <file_path> <username> <permission>
    """

    if user_state['level'] != LEVEL.get('1'):
        client_socket.sendall(f"530 Permission denied. Only Super Admin can change user levels.\n".encode(FORMAT))
        return

    if len(command_parts) < 4:
        client_socket.sendall(f"501 Syntax error in parameters\n".encode())
        return

    file_path, directory = utilities.resolve_path(user_state['current_directory'],
                                                  command_parts[1])  # Simplified without util.resolve_path
    username = command_parts[2]
    permission = command_parts[3]

    if permission not in PERMISSIONS_MAP:
        client_socket.sendall(
            f"501 Invalid permission '{permission}'. Allowed: {list(PERMISSIONS_MAP.keys())}\n".encode())
        return

    if not os.path.exists(file_path):
        client_socket.sendall(f"550 File '{file_path}' not found\n".encode())
        return

    try:
        success = set_permissions_windows(file_path, username, permission)
        if success:
            client_socket.sendall(f"250 Permissions updated for {username} on {file_path}\n".encode())
        else:
            client_socket.sendall(f"450 Failed to update permissions\n".encode())
    except Exception as e:
        client_socket.sendall(f"450 Failed to update permissions: {e}\n".encode())


# -----------------------------------------------------------------------------------------------------------------------
def change_user_level(command_parts, user_state, client_socket):
    """
    تغییر سطح دسترسی یک کاربر.
    دستور: CHANGELEVEL <username> <new_level>
    """
    current_user_level = user_state.get('level')
    if current_user_level != LEVEL.get("1"):
        client_socket.sendall(f"530 Permission denied. Only Super Admin can change user levels.\n".encode(FORMAT))
        return

    if len(command_parts) < 3:
        client_socket.sendall(f"501 Syntax error in parameters\n".encode(FORMAT))
        return

    target_user = command_parts[1]
    new_level = command_parts[2]

    if target_user not in VALID_USERS:
        client_socket.sendall(f"550 User '{target_user}' not found.\n".encode(FORMAT))
        return

    try:
        current_level_value = int(list(LEVEL.keys())[list(LEVEL.values()).index(current_user_level)])
        target_user_level_value = int(list(LEVEL.keys())[list(LEVEL.values()).index(VALID_USERS[target_user]['level'])])
        new_level_value = int(new_level)
    except (ValueError, KeyError) as e:
        client_socket.sendall(f"501 Invalid level or level not found: {new_level}.\n".encode(FORMAT))
        return

    if new_level_value < current_level_value:
        client_socket.sendall(f"530 Permission denied. You cannot set level higher than your own.\n".encode(FORMAT))
        return

    if target_user_level_value <= current_level_value:
        client_socket.sendall(
            f"530 Permission denied. You cannot modify a user with higher or equal level to your own.\n".encode(FORMAT))
        return

    VALID_USERS[target_user]['level'] = LEVEL.get(new_level)
    client_socket.sendall(f"250 User '{target_user}' level changed to '{LEVEL.get(new_level)}'.\n".encode(FORMAT))


#-----------------------------------------------------------------------------------------------------------------------
def create_user_folders(user_state):
    file_name, par = utilities.resolve_path(BASE_DIRECTORY, user_state['username'])
    if os.path.isdir(file_name):
        return

    upload_directory = os.path.join(BASE_DIRECTORY, user_state['username'])
    if not os.path.exists(upload_directory):
        os.makedirs(upload_directory)
    if user_state['level'] == LEVEL.get('1'):
        set_permissions_windows(upload_directory, user_state['username'], "Full")
    elif user_state['level'] == LEVEL.get('2'):
        set_permissions_windows(upload_directory, user_state['username'], "Read")
        set_permissions_windows(upload_directory, user_state['username'], "Write")
        set_permissions_windows(upload_directory, user_state['username'], "Delete")
        set_permissions_windows(upload_directory, user_state['username'], "Create File")
    elif user_state['level'] == LEVEL.get('3'):
        set_permissions_windows(upload_directory, user_state['username'], "Read")
        set_permissions_windows(upload_directory, user_state['username'], "Write")
        set_permissions_windows(upload_directory, user_state['username'], "Delete")
    else:
        set_permissions_windows(upload_directory, user_state['username'], "Read")

    parent_dir = os.path.dirname(user_state['current_directory'])
    client_folder = parent_dir + "\\client-folder"
    download_directory = os.path.join(client_folder, user_state['username'])
    if not os.path.exists(download_directory):
        os.makedirs(download_directory)

    set_permissions_windows(download_directory, user_state['username'], "Full")


  #  -----------------------------------------------------------------------------------------------------------------------


def sign_up(command_parts, user_state, client_socket):
    """
    Handles user registration and sets default permissions for new users.
    """
    username = command_parts[1]
    password = command_parts[2]

    if username in VALID_USERS:
        client_socket.sendall(f"450 Username already exists\n".encode(FORMAT))
        return user_state

    if len(password) < 5 or not re.search(r"[a-zA-Z]", password) or not re.search(r"\d", password):
        client_socket.sendall(
            f"430 Invalid password (must contain at least 5 characters, including letters and numbers)\n".encode(
                FORMAT))
        return user_state

    VALID_USERS[username] = {'password': password}
    user_state['username'] = username
    user_state['authenticated'] = True
    user_state['status'] = 'authenticated'

    user_state['level'] = LEVEL.get('4')

    try:
        create_user_folders(user_state)

        client_socket.sendall(f"230 Registration successful. Permissions set.\n".encode(FORMAT))
    except Exception as e:
        client_socket.sendall(f"450 Error setting permissions: {str(e)}\n".encode(FORMAT))

    return user_state


# -----------------------------------------------------------------------------------------------------------------------
def set_default_permissions(file_path, user_state):
    set_permissions_windows(file_path, user_state['username'], 'Full')
    set_permissions_windows(file_path, LEVEL.get("4"), 'Read')

    set_permissions_windows(file_path, LEVEL.get("3"), 'Read')
    set_permissions_windows(file_path, LEVEL.get("3"), 'Write')

    set_permissions_windows(file_path, LEVEL.get("2"), 'Read')
    set_permissions_windows(file_path, LEVEL.get("2"), 'Write')
    set_permissions_windows(file_path, LEVEL.get("2"), 'Delete')
    set_permissions_windows(file_path, LEVEL.get("2"), 'Create File')

    set_permissions_windows(file_path, LEVEL.get("1"), 'Full')


# ----------------------------------------------------------------------------------------------------------------------


def handle_user(command_parts, user_state, client_socket):
    """Handles user login by username."""
    username = command_parts[1]
    if username in VALID_USERS:
        user_state['username'] = username
        user_state['status'] = 'waiting_for_pass'
        client_socket.sendall(f"331 Username OK, need password\n".encode(FORMAT))
    else:
        client_socket.sendall(f"530 Invalid username\n".encode(FORMAT))

    return user_state


# ----------------------------------------------------------------------------------------------------------------------

def handle_pass(command_parts, user_state, client_socket):
    """Handles user password verification."""
    if user_state['status'] == 'waiting_for_pass':
        password = command_parts[1]
        username = user_state['username']
        if username in VALID_USERS:
            if VALID_USERS[username]['password'] == password:
                user_state['authenticated'] = True
                user_state['status'] = 'authenticated'
                user_state['level'] = VALID_USERS[username]['level']
                create_user_folders(user_state)
                client_socket.sendall(f"230 User logged in, proceed\n".encode(FORMAT))
            else:
                client_socket.sendall(f"530 Login incorrect\n".encode(FORMAT))
        else:
            client_socket.sendall(f"530 Please enter USER command first\n".encode(FORMAT))
        return user_state


# ----------------------------------------------------------------------------------------------------------------------

def handle_list(user_state, command_parts, client_socket, data_socket):
    """Lists files in the current or specified directory."""
    global IS_TRANSFERRING

    if not user_state['authenticated']:
        client_socket.sendall(f"530 Not logged in\n".encode(FORMAT))
        return

    directory = user_state['current_directory']
    if len(command_parts) > 1:
        _, directory = utilities.resolve_path(directory, command_parts[1])

    if not os.path.isdir(directory):
        client_socket.sendall(f"550 Directory not found\n".encode(FORMAT))
        return

    client_socket.sendall(f"125 Here comes the directory listing\n".encode(FORMAT))
    conn, addr = data_socket.accept()  # Accept the data connection
    try:
        IS_TRANSFERRING[client_socket] = True
        for item in os.listdir(directory):
            if item.endswith('.perm'):
                continue
            item_path = os.path.join(directory, item)
            permissions = get_permissions(item_path, client_socket)
            size = os.path.getsize(item_path)
            mod_time = datetime.fromtimestamp(os.path.getmtime(item_path)).strftime("%b %d %H:%M")
            conn.sendall(
                f"permissions: {permissions}     size: {size}     modified time: {mod_time}     items: {item}\n".encode(
                    FORMAT))
        conn.close()
        client_socket.sendall(f"226 Directory send OK\n".encode(FORMAT))
    except Exception as e:
        print(f"Error in LIST: {e}")
        client_socket.sendall(f"450 Transfer failed\n".encode(FORMAT))
    finally:
        IS_TRANSFERRING[client_socket] = False


# ----------------------------------------------------------------------------------------------------------------------

def handle_retr(user_state, command_parts, client_socket, data_socket):
    """Facilitates downloading a file from the server."""
    global IS_TRANSFERRING

    if not user_state['authenticated']:
        client_socket.sendall(f"530 Not logged in\n".encode(FORMAT))
        return

    if len(command_parts) < 2:
        client_socket.sendall(f"501 Syntax error in parameters or arguments\n".encode(FORMAT))
        return
    filename = command_parts[1]
    filepath, user_state['current_directory'] = utilities.resolve_path(user_state['current_directory'], filename)

    if not os.path.isfile(filepath):
        client_socket.sendall(f"550 File not found\n".encode(FORMAT))
        return

    if not check_permission(filepath, user_state, 'Read'):
        client_socket.sendall(f"550 Permission denied\n".encode(FORMAT))
        return

    file_lock = get_file_lock(filepath)
    try:
        # Acquire lock with timeout
        if not file_lock.acquire(timeout=operation_timeout):
            client_socket.send(f"450 File '{filename}' is busy. Try again later.".encode(FORMAT))
            return

        client_socket.sendall(f"150 Opening data connection\n".encode(FORMAT))
        conn, _ = data_socket.accept()
        IS_TRANSFERRING[client_socket] = True
        with open(filepath, 'rb') as file:
            while chunk := file.read(1024):
                conn.sendall(chunk)
        conn.close()

        client_socket.sendall(f"226 Transfer complete\n".encode(FORMAT))
    except Exception as e:
        print(f"Error in RETR: {e}")
        client_socket.sendall(f"450 Transfer failed\n".encode(FORMAT))
    finally:
        file_lock.release()
        IS_TRANSFERRING[client_socket] = False


# ----------------------------------------------------------------------------------------------------------------------

def handle_stor(user_state, command_parts, client_socket, data_socket):
    """Handles file upload to the server."""
    global IS_TRANSFERRING

    if not user_state['authenticated']:
        client_socket.sendall(f"530 Not logged in\n".encode(FORMAT))
        return

    if len(command_parts) < 2:
        client_socket.sendall(f"501 Syntax error in parameters or arguments\n".encode(FORMAT))
        return

    if command_parts[2] == ".":
        file_path, user_state['current_directory'] = utilities.resolve_path(user_state['current_directory'],
                                                                            user_state['username'])
        if not os.path.isdir(file_path):
            file_path = user_state['current_directory']
        file_name = os.path.join(file_path, command_parts[1])
    else:
        file_path, _ = utilities.resolve_path(user_state['current_directory'], command_parts[2])
        file_name = os.path.join(file_path, command_parts[1])

    if not check_permission(file_path, user_state, 'Write'):
        client_socket.sendall(f"550 Permission denied\n".encode(FORMAT))
        return

    file_lock = get_file_lock(file_path)
    try:
        # Acquire lock with timeout
        if not file_lock.acquire(timeout=operation_timeout):
            client_socket.send(f"450 File '{file_name}' is busy. Try again later.".encode(FORMAT))
            return
        client_socket.sendall(f"150 Ready to receive file data\n".encode(FORMAT))
        conn, _ = data_socket.accept()
        IS_TRANSFERRING[client_socket] = True
        with open(file_name, 'wb') as file:
            while chunk := conn.recv(1024):
                file.write(chunk)
        conn.close()
        client_socket.sendall(f"226 File transfer complete\n".encode(FORMAT))
        user_state['current_directory'] = file_path
        set_default_permissions(file_name, user_state)
    except Exception as e:
        print(f"Error in STOR: {e}")
        client_socket.sendall(f"450 Transfer failed\n".encode(FORMAT))
    finally:
        file_lock.release()
        IS_TRANSFERRING[client_socket] = False


# ----------------------------------------------------------------------------------------------------------------------

def delete_assistor(path, client_socket):
    file_lock = get_file_lock(path)

    try:
        if not file_lock.acquire(timeout=operation_timeout):
            client_socket.send(f"450 File '{path}' is busy. Try again later.".encode(FORMAT))
            return
        perm_file = f'{path}.perm'
        os.remove(path)
        os.remove(perm_file)
        client_socket.sendall(f"250 File deleted successfully\n".encode(FORMAT))
    except Exception as e:
        print(f"Error in DELETE: {e}")
        client_socket.sendall(f"450 File deletion failed\n".encode(FORMAT))
    finally:
        file_lock.release()


# ----------------------------------------------------------------------------------------------------------------------

def handle_delete(user_state, command_parts, client_socket):  # todo: lock ro ok konnnnnnnnnnnnnnn
    """Deletes a specified file."""
    if not user_state['authenticated']:
        client_socket.sendall(f"530 Not logged in\n".encode(FORMAT))
        return

    if len(command_parts) < 2:
        client_socket.sendall(f"501 Syntax error in parameters or arguments\n".encode(FORMAT))
        return

    public_path, user_state['current_directory'] = utilities.resolve_path(user_state['current_directory'],
                                                                          command_parts[1])
    user_dir = os.path.join(user_state['current_directory'], user_state['username'])
    private_path, user_state['current_directory'] = utilities.resolve_path(user_dir, command_parts[1])

    is_public_file = os.path.isfile(public_path)
    is_private_file = os.path.isfile(private_path)
    public_permission = None
    private_permission = None

    if is_public_file:
        public_permission = check_permission(public_path, user_state, 'Delete')

    if is_private_file:
        private_permission = check_permission(private_path, user_state, 'Delete')

    if not is_public_file and not is_private_file:
        client_socket.sendall(f"550 File not found\n".encode(FORMAT))
        return
    # ------------------------------------------------------------------------------------------------------------------
    if public_permission:
        delete_assistor(client_socket=client_socket, path=public_path)

    if private_permission:
        # just private
        delete_assistor(client_socket=client_socket, path=private_path)

    if not public_permission and not private_permission:
        client_socket.sendall(f"550 Permission denied\n".encode(FORMAT))
        return


# ----------------------------------------------------------------------------------------------------------------------

def handle_mkd(user_state, command_parts, client_socket):
    """Creates a new directory."""
    if not user_state['authenticated']:
        client_socket.sendall(f"530 Not logged in\n".encode(FORMAT))
        return

    if len(command_parts) < 2:
        client_socket.sendall(f"501 Syntax error in parameters or arguments\n".encode(FORMAT))
        return
    # todo: mishe check kard agar akharesh esm user bod dige ezafe nakone
    user_folder = os.path.join(user_state['current_directory'], user_state['username'])
    dir_path, parent = utilities.resolve_path(user_folder, command_parts[1])
    if not check_permission(parent, user_state, 'Create Subdirectory'):
        client_socket.sendall(f"550 Permission denied\n".encode(FORMAT))
        return
    try:
        os.makedirs(dir_path)
        set_permissions_windows(dir_path, user_state['username'], "Full")
        client_socket.sendall(f"257 Directory created successfully\n".encode(FORMAT))
        user_state['current_directory'] = dir_path
    except Exception as e:
        print(f"Error in MKD: {e}")
        client_socket.sendall(f"550 Unable to create directory\n".encode(FORMAT))


# ----------------------------------------------------------------------------------------------------------------------

def handle_rmd(user_state, command_parts, client_socket):  # todo: lock ro ok konnnnnnnnnnnnnnn   alan fek konam okeye
    """Removes an existing directory."""
    if not user_state['authenticated']:
        client_socket.sendall(f"530 Not logged in\n".encode(FORMAT))
        return

    if len(command_parts) < 2:
        client_socket.sendall(f"501 Syntax error in parameters or arguments\n".encode(FORMAT))
        return
    parent_dir = os.path.dirname(user_state['current_directory'])
    dir_path, parent = utilities.resolve_path(parent_dir, command_parts[1])
    if not check_permission(dir_path, user_state, 'Delete'):
        client_socket.sendall(f"550 Permission denied\n".encode(FORMAT))
        return
    if not os.path.isdir(dir_path):
        client_socket.sendall(f"550 Directory not found\n".encode(FORMAT))
        return

    file_locks = {}
    for item in os.listdir(dir_path):
        item_path = os.path.join(dir_path, item)
        file_locks[str(item)] = get_file_lock(item_path)
        if not file_locks[str(item)].acquire(timeout=operation_timeout):
            client_socket.send(f"450 File '{item_path}' is busy. Try again later.".encode(FORMAT))
            return

    try:
        os.rmdir(dir_path)
        perm_file = f'{dir_path}.perm'
        os.remove(perm_file)
        client_socket.sendall(f"250 Directory removed successfully\n".encode(FORMAT))
        user_state['current_directory'] = parent
    except OSError:
        client_socket.sendall(f"550 Directory not empty or cannot be removed\n".encode(FORMAT))
    finally:
        for item in file_locks.values():
            item.release()


# ----------------------------------------------------------------------------------------------------------------------

def handle_pwd(user_state, client_socket):
    """Outputs the current working directory."""
    if not user_state['authenticated']:
        client_socket.sendall(f"530 Not logged in\n".encode(FORMAT))
        return

    client_socket.sendall(f'257 "{user_state["current_directory"]}"\n'.encode(FORMAT))


# ----------------------------------------------------------------------------------------------------------------------

def handle_cwd(user_state, command_parts, client_socket):
    """Changes the current working directory."""
    if not user_state['authenticated']:
        client_socket.sendall(f"530 Not logged in\n".encode(FORMAT))
        return

    if len(command_parts) < 2:
        client_socket.sendall(f"501 Syntax error in parameters or arguments\n".encode(FORMAT))
        return

    file_name, par = utilities.resolve_path(user_state['current_directory'], command_parts[1])
    if not os.path.isdir(file_name):
        client_socket.sendall(f"550 Directory not found\n".encode(FORMAT))
        return

    if not check_permission(file_name, user_state, 'Read'):
        client_socket.sendall(f"550 Permission denied\n".encode(FORMAT))
        return

    user_state['current_directory'] = file_name
    client_socket.sendall(f"250 Directory changed successfully\n".encode(FORMAT))


# ----------------------------------------------------------------------------------------------------------------------

def handle_cdup(user_state, client_socket):
    """Moves to the parent directory."""
    if not user_state['authenticated']:
        client_socket.sendall(f"530 Not logged in\n".encode(FORMAT))
        return

    parent_dir = os.path.dirname(user_state['current_directory'])
    if not os.path.isdir(parent_dir):
        client_socket.sendall(f"550 Cannot change to parent directory\n".encode(FORMAT))
        return

    if not check_permission(parent_dir, user_state, 'Read'):
        client_socket.sendall(f"550 Permission denied\n".encode(FORMAT))
        return

    user_state['current_directory'] = parent_dir
    client_socket.sendall(f"250 Directory changed to parent successfully\n".encode(FORMAT))


# ----------------------------------------------------------------------------------------------------------------------

# Main Client Handler --------------------------------------------------------------------------------------------------

def handle_client(client_socket, data_socket, addr):
    """Manages the lifecycle of a client connection."""
    user_state = {
        'username': None,
        'authenticated': False,
        'status': None,
        'current_directory': BASE_DIRECTORY,
        'level': None
    }

    client_socket.sendall(f"220 FTP Server Ready\n".encode(FORMAT))

    connected = True
    while connected:
        try:
            msg_length = client_socket.recv(HEADER).decode(FORMAT)
            if msg_length:
                msg_length = int(msg_length)
                command = client_socket.recv(msg_length).decode(FORMAT)
                print(f"[{addr}] said: {command}\n")
                command_parts = command.split()
                cmd = command_parts[0].upper()

                if cmd == "SIGNUP":
                    user_state = sign_up(command_parts, user_state, client_socket)
                elif cmd == "USER":
                    user_state = handle_user(command_parts, user_state, client_socket)
                elif cmd == "PASS":
                    user_state = handle_pass(command_parts, user_state, client_socket)
                elif cmd == "LIST":
                    handle_list(user_state, command_parts, client_socket, data_socket)
                elif cmd == "RETR":
                    handle_retr(user_state, command_parts, client_socket, data_socket)
                elif cmd == "STOR":
                    handle_stor(user_state, command_parts, client_socket, data_socket)
                elif cmd == "DELE":
                    handle_delete(user_state, command_parts, client_socket)
                elif cmd == "MKD":
                    handle_mkd(user_state, command_parts, client_socket)
                elif cmd == "RMD":
                    handle_rmd(user_state, command_parts, client_socket)
                elif cmd == "PWD":
                    handle_pwd(user_state, client_socket)
                elif cmd == "CWD":
                    handle_cwd(user_state, command_parts, client_socket)
                elif cmd == "CDUP":
                    handle_cdup(user_state, client_socket)
                elif cmd == "SETACL" and user_state['level'] == LEVEL.get('1'):
                    handle_setacl(command_parts, client_socket, user_state)
                elif cmd == "CHANGELEVEL" and user_state['level'] == LEVEL.get('1'):
                    change_user_level(command_parts, user_state, client_socket)
                elif cmd == "HELP":
                    handle_help(user_state, client_socket)
                elif cmd == "QUIT":
                    if IS_TRANSFERRING[client_socket]:
                        client_socket.sendall(f"[WARNING!] Cannot quit during file transfer.\n".encode(FORMAT))
                    else:
                        client_socket.sendall(f"221 Goodbye\n".encode(FORMAT))
                        connected = False
                else:
                    client_socket.sendall(f"502 Command not implemented\n".encode(FORMAT))
        except Exception as e:
            print(f"Error handling client {addr}: {e}")
            connected = False

    client_socket.close()
    print(f"[DISCONNECTED] {addr} disconnected.")
    current_thread = threading.current_thread()
    # ZOMBIE_THREADS[str(current_thread)] = current_thread


#Main Server-----------------------------------------------------------------------------------------------------------

def start_server():
    global ENCRYPTION_MODE
    """
    Starts the FTP server, listens for connections, and spawns client threads.
    """
    control_socket = None
    data_socket = None

    if ENCRYPTION_MODE == "SSL":
        control_socket = SSL_Encryption.ssl_control_connection_server()
        data_socket = SSL_Encryption.ssl_data_connection_server()

    if ENCRYPTION_MODE == "SSL/TLS":
        control_socket = SSL_TLS_Encryption.ssl_tls_control_connection_server()
        data_socket = SSL_TLS_Encryption.ssl_tls_data_connection_server()

    elif ENCRYPTION_MODE == "SSH":  # todo;fix this shit
        pass

    elif ENCRYPTION_MODE == "TLS":
        control_socket = TLS_Encryption.tls_control_connection_server()
        data_socket = TLS_Encryption.tls_data_connection_server()

    else:  # its PLAIN mode without any encryption protocol
        control_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        data_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)

    control_socket.bind((SERVER_IP, CONTROL_PORT))
    data_socket.bind((SERVER_IP, DATA_PORT))
    control_socket.listen()
    data_socket.listen()

    print(
        f"{FTP_TYPE} is listening. Control on {SERVER_IP}:{CONTROL_PORT}, Data on {SERVER_IP}:{DATA_PORT}")

    try:
        while True:
            client_socket, addr = control_socket.accept()
            print(f"[NEW CONNECTION] {addr} connected.")
            thread = threading.Thread(target=handle_client, args=(client_socket, data_socket, addr))
            thread.start()
            print(f"[ACTIVE CONNECTIONS] {threading.active_count() - 1}")
            for thread in list(ZOMBIE_THREADS):  # Copy keys to avoid dictionary modification during iteration
                if not ZOMBIE_THREADS[thread].is_alive():
                    ZOMBIE_THREADS[thread].join()  # Join the finished client thread
                    del ZOMBIE_THREADS[thread]

    except KeyboardInterrupt:
        print("\n[SHUTDOWN] Server is shutting down.")
    finally:
        control_socket.close()
        data_socket.close()


# -----------------------------------------------------------------------------------------------------------------------
if __name__ == "__main__":
    start_server()
