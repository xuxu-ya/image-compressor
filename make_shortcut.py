import struct, os

target = r"C:\Users\Administrator\WorkBuddy\2026-08-18-16-32-26\image-compressor\start_silent.bat"
workdir = r"C:\Users\Administrator\WorkBuddy\2026-08-18-16-32-26\image-compressor"

LINK_CLSID = bytes([0x01,0x14,0x02,0x00, 0x00,0x00,0x00,0x00,
                    0xC0,0x00,0x00,0x00, 0x00,0x00,0x00,0x46])

# ---- LinkInfo（本地路径方式，含完整 VolumeID）----
# VolumeID：VolumeIDSize(4)+DriveType(4)+Serial(4)+VolumeLabelOffset(4)+VolumeLabelOffsetUnicode(4)=20
vol_id = (struct.pack('<I', 0x00000014) +   # VolumeIDSize = 20
          struct.pack('<I', 3) +            # DriveType = DRIVE_FIXED
          struct.pack('<I', 0) +            # DriveSerialNumber
          struct.pack('<I', 0x00000014) +   # VolumeLabelOffset = 0x14 => 用 Unicode 偏移字段
          struct.pack('<I', 0))             # VolumeLabelOffsetUnicode = 0（无卷标）
local_base = target.encode('ascii') + b'\x00'

LINK_INFO_HEADER_SIZE = 0x1C           # 28
vol_id_offset = LINK_INFO_HEADER_SIZE  # 28
local_base_offset = vol_id_offset + len(vol_id)  # 48

link_info = (struct.pack('<I', 0) +                  # LinkInfoSize（稍后回填）
             struct.pack('<I', LINK_INFO_HEADER_SIZE) +
             struct.pack('<I', 0x00000001) +         # LinkInfoFlags: 含本地路径
             struct.pack('<I', vol_id_offset) +
             struct.pack('<I', local_base_offset) +
             struct.pack('<I', 0) +                  # CommonNetworkRelativeLinkOffset
             struct.pack('<I', 0) +                  # CommonNetworkRelativeLinkSize
             vol_id + local_base)
link_info = struct.pack('<I', len(link_info)) + link_info[4:]

# ---- 字符串段（仅工作目录，ANSI 长度计数）----
def ansi_string(s):
    b = s.encode('ascii')
    return struct.pack('<H', len(b)) + b + b'\x00'

working_dir_field = ansi_string(workdir)

# ---- ShellLinkHeader（固定 76 字节）----
link_flags = 0x00000002 | 0x00000010    # HasLinkInfo | HasWorkingDir
header = (struct.pack('<I', 0x0000004C) +  # HeaderSize = 76
          LINK_CLSID +
          struct.pack('<I', link_flags) +
          struct.pack('<I', 0x00000020) +  # FileAttributes: ARCHIVE
          struct.pack('<Q', 0) +           # CreationTime
          struct.pack('<Q', 0) +           # AccessTime
          struct.pack('<Q', 0) +           # WriteTime
          struct.pack('<I', 0) +           # FileSize
          struct.pack('<I', 0) +           # IconIndex
          struct.pack('<I', 7) +           # ShowCommand: SW_SHOWMINNOACTIVE
          struct.pack('<H', 0) +           # HotKey
          struct.pack('<H', 0) +           # Reserved1
          struct.pack('<H', 0) +           # Reserved2
          struct.pack('<H', 0) +           # Reserved3
          struct.pack('<I', 0))            # 补齐到 76 字节

lnk = header + link_info + working_dir_field

startup = os.path.join(os.environ['APPDATA'],
                       'Microsoft', 'Windows', 'Start Menu', 'Programs', 'Startup')
os.makedirs(startup, exist_ok=True)
out = os.path.join(startup, 'ImageCompressorAutoStart.lnk')
with open(out, 'wb') as f:
    f.write(lnk)
print('WROTE', out, len(lnk), 'bytes')
print('Header', len(header), 'LinkInfo', len(link_info), 'WD', len(working_dir_field))
