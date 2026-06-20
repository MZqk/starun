#!/usr/bin/env python3
import os
import sys
import urllib.request
import zipfile
import platform
import shutil
import ssl
import argparse

# 忽略 HTTPS 证书校验，防止在某些容器或精简系统中因为缺少 CA 证书而报错
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except AttributeError:
    pass

def download_starnet():
    parser = argparse.ArgumentParser(description="自动下载并解压部署 StarNet2 二进制程序。")
    parser.add_argument(
        "--platform",
        choices=["local", "linux", "macos-arm64"],
        default="local",
        help="指定目标下载的平台环境 (local: 自动检测宿主机, linux: 强制 Linux x86_64, macos-arm64: 强制 macOS ARM64)"
    )
    parser.add_argument(
        "--out",
        help="指定输出部署的目标目录路径"
    )
    args = parser.parse_args()

    # 确定目标系统和架构
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    # 2.5.2 版本 CLI 下载地址
    LINUX_URL = "https://download.starnetastro.com/starnet2_linux_2.5.2-0207_ORT_x64_cli.zip"
    MACOS_ARM_URL = "https://download.starnetastro.com/starnet2_macos-arm64_2.5.2-0207_COREML_arm64_cli.zip"
    
    url = None
    target_platform = args.platform
    
    if target_platform == "local":
        print(f"[*] 检测到当前宿主机环境系统: {system}, 架构: {machine}")
        if system == "linux":
            url = LINUX_URL
            target_platform = "linux"
        elif system == "darwin" and ("arm64" in machine or "aarch64" in machine):
            url = MACOS_ARM_URL
            target_platform = "macos-arm64"
        elif system == "darwin":
            url = "https://download.starnetastro.com/starnet2_macos-x64_2.5.2-0207_ORT_x64_cli.zip"
            target_platform = "macos-x64"
            print("[*] 正在下载 macOS x64 (Intel) 版本的 StarNet2...")
        else:
            print(f"[!] 不支持的系统/架构类型: {system} {machine}。跳过 starnet2 自动下载。")
            return
    elif target_platform == "linux":
        url = LINUX_URL
    elif target_platform == "macos-arm64":
        url = MACOS_ARM_URL

    # 确定解压目标路径
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if args.out:
        target_dir = os.path.abspath(args.out)
    else:
        if target_platform == "linux":
            # Linux 版默认下载到宿主机的 api/starnet2 目录，方便 Docker Build 时打包
            target_dir = os.path.abspath(os.path.join(script_dir, "..", "..", "api", "starnet2"))
        else:
            target_dir = os.path.join(script_dir, "StarNet2")

    # 创建目标文件夹
    os.makedirs(target_dir, exist_ok=True)
    
    # 检查是否已经存在核心的可执行文件，防止重复下载
    exe_name = "starnet++"
    if os.path.isfile(os.path.join(target_dir, exe_name)):
        print(f"[*] StarNet2 核心可执行文件已存在于 {target_dir}，跳过下载。")
        return

    zip_path = os.path.join(target_dir, "starnet2.zip")
    print(f"[*] 开始下载 StarNet2 ({target_platform})，来源地址: {url}")
    
    try:
        # 下载文件
        urllib.request.urlretrieve(url, zip_path)
        print("[*] 下载完成，正在进行解压...")
        
        with zipfile.ZipFile(zip_path, 'r') as zip_ref:
            import tempfile
            with tempfile.TemporaryDirectory() as tmp_extract_dir:
                zip_ref.extractall(tmp_extract_dir)
                
                # 寻找解压目录中 starnet++ 二进制文件所在的实际目录（防 zip 内部包有一层文件夹）
                found_exe_dir = None
                for root, dirs, files in os.walk(tmp_extract_dir):
                    if "starnet++" in files or "starnet2" in files:
                        found_exe_dir = root
                        break
                
                if found_exe_dir:
                    # 将包含 starnet++ 文件夹下的所有文件移动至 target_dir 中
                    for item in os.listdir(found_exe_dir):
                        s = os.path.join(found_exe_dir, item)
                        d = os.path.join(target_dir, item)
                        if os.path.isdir(s):
                            if os.path.exists(d):
                                shutil.rmtree(d)
                            shutil.copytree(s, d)
                        else:
                            shutil.copy2(s, d)
                    print(f"[+] StarNet2 已成功下载并部署至 {target_dir}")
                else:
                    raise RuntimeError("在解压后的内容中未能找到 starnet++ 或 starnet2 二进制可执行文件。")
                    
        # 显式赋予可执行权限
        for name in ("starnet++", "starnet2"):
            executable_path = os.path.join(target_dir, name)
            if os.path.exists(executable_path):
                os.chmod(executable_path, 0o755)
                print(f"[+] 已为二进制文件赋予可执行权限: {executable_path}")
            
    except Exception as e:
        print(f"[x] 下载或安装 StarNet2 失败: {e}", file=sys.stderr)
        # 如果下载失败，清理下载的 zip 缓存文件，不阻塞后续流程（由后端脚本执行时进行兜底回退）
        sys.exit(0)
    finally:
        if os.path.exists(zip_path):
            os.remove(zip_path)

if __name__ == "__main__":
    download_starnet()
