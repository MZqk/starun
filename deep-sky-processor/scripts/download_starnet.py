#!/usr/bin/env python3
import os
import sys
import urllib.request
import zipfile
import platform
import shutil
import ssl

# 忽略 HTTPS 证书校验，防止在某些容器或精简系统中因为缺少 CA 证书而报错
try:
    ssl._create_default_https_context = ssl._create_unverified_context
except AttributeError:
    pass

def download_starnet():
    # 确定目标系统和架构
    system = platform.system().lower()
    machine = platform.machine().lower()
    
    print(f"[*] 检测到当前环境系统: {system}, 架构: {machine}")
    
    # 2.5.2 版本 CLI 下载地址
    LINUX_URL = "https://download.starnetastro.com/starnet2_linux_2.5.2-0207_ORT_x64_cli.zip"
    MACOS_ARM_URL = "https://download.starnetastro.com/starnet2_macos-arm64_2.5.2-0207_COREML_arm64_cli.zip"
    
    url = None
    # 确定解压目标路径在 deep-sky-processor/scripts/StarNet2
    script_dir = os.path.dirname(os.path.abspath(__file__))
    target_dir = os.path.join(script_dir, "StarNet2")
    
    if system == "linux":
        # 即使是 ARM 架构（如在 Apple Silicon 宿主机上没有指定平台直接构建 Docker 镜像），
        # 由于 StarNet2 官方未发布 Linux ARM64 版，我们也必须拉取 Linux x86_64 二进制文件。
        url = LINUX_URL
        if not ("x86_64" in machine or "amd64" in machine):
            print("[!] 警告：Linux ARM64 架构下没有原生 StarNet2 二进制包，将尝试下载 Linux x86_64 并在 x64 模拟环境中运行")
    elif system == "darwin" and ("arm64" in machine or "aarch64" in machine):
        url = MACOS_ARM_URL
    elif system == "darwin":
        # macOS Intel 芯片的临时兼容
        url = "https://download.starnetastro.com/starnet2_macos-x64_2.5.2-0207_ORT_x64_cli.zip"
        print("[*] 正在下载 macOS x64 (Intel) 版本的 StarNet2...")
    else:
        print(f"[!] 不支持的系统/架构类型: {system} {machine}。跳过 starnet2 自动下载。")
        return

    # 创建目标文件夹
    os.makedirs(target_dir, exist_ok=True)
    
    # 检查是否已经存在核心的可执行文件，防止重复下载
    exe_name = "starnet++"
    if os.path.isfile(os.path.join(target_dir, exe_name)):
        print(f"[*] StarNet2 核心可执行文件已存在于 {target_dir}，跳过下载。")
        return

    zip_path = os.path.join(target_dir, "starnet2.zip")
    print(f"[*] 开始下载 StarNet2，来源地址: {url}")
    
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
