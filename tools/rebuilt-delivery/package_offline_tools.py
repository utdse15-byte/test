"""Build a small independent browser toolkit from a committed delivery source.

It intentionally does not pretend to include the native movie application,
Python dependencies, FFmpeg or external model execution.
"""
from pathlib import Path
from hashlib import sha256
import argparse,json,subprocess,tempfile,zipfile


def build(repo:Path,output:Path):
    if output.exists() or output.is_symlink():raise FileExistsError('Never overwrite an earlier downloadable toolkit')
    status=subprocess.check_output(['git','status','--porcelain'],cwd=repo)
    if status.strip():raise ValueError('Commit the source first')
    version=json.loads((repo/'DELIVERY_VERSION.json').read_text())
    stage=version['stage']
    if stage not in {'R7','R8','R9','R10'}:raise ValueError('This compact toolkit requires R7 or later')
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo).decode().strip()
    html=(repo/'tools/model_workbench.html').read_bytes()
    if html!=(repo/'src/manju/authoring/data/workbench.html').read_bytes():raise ValueError('Standalone and installed workbench differ')
    intro='''# Manju R7 离线模型与审片工具

## 先打开这个文件

完整解压到新目录，双击 OPEN_MODEL_WORKBENCH.html。
不需要安装 Python，不需要账号，不消耗模型额度。不要在 ZIP 预览窗口内直接运行。

这份小包包含完整的本地镜头任务规划、素材哈希、模型约束检查、人工确认、交接 ZIP、候选视频对比、审片记录和草稿晋升。
它不生成商业模型视频，也不包含原影片工作台的完整安装文件。完整源码、Git 历史、本地生成与成片功能请另存 MANJU_R7_CUMULATIVE.zip。

## 开始使用

填写镜头与提示词，或者用“导入任务 / 镜头”导入 EXAMPLE_REQUEST.json。
添加实际素材后检查适配，明确选择方案并确认，再保存任务 JSON 或交接 ZIP。
模型能力档是带日期的作者约束，不是已经接通的 API；提交外部平台前要核对其当前限制。

生成视频返回后，在审片区添加候选，观看并记录决定。审片 JSON 不包含所有视频，必须同时保存原视频。
只有明确批准的实际草稿可以晋升，定稿交接 ZIP 会包含批准草稿和审片记录。
没有自动选片，没有自动 Picture Lock。评分是你的记录，不是机器画质排名。

每次导出都请到浏览器下载列表确认保存。关闭页面前优先保存“完整工作现场 ZIP”：包括实际素材、未完成文字、能力档和审片历史。恢复先核验全部文件，再明确确认；当前批准勾选会清除。浏览器草稿不是永久备份。

R9 增加返回视频规格检查：时长、画幅与本地最低短边。未知规格不当作通过；浏览器检查不等于完整视频解码、画质合格或人工批准。
R10 导入能力档后先展示字段差异、当前镜头影响和证据复核窗口，确认后才替换。更换任务后必须重新预览。可预览恢复上一档。已有 Omni Flash 与新 Gen-4.5 仍仅为离线作者档，不接通商业服务。

## 体积与边界

单文件128 MiB、总素材512 MiB是本页面的本地资源上限，不是供应商支持声明。
视频并排播放并非逐帧锁定同步，A/B 标签也不代表科学双盲。
项目来自真实 R2 的功能重建；原版 R3/R4 ZIP 未找回。没有 Windows 双击实机认证或真实商业生成评测。

## 文件

OPEN_MODEL_WORKBENCH.html：完整离线页面。
MODEL_CATALOG.json：可单独导入/存档的能力档。
EXAMPLE_REQUEST.json：不带媒体的合成任务示例。
VERSION.json：提交、版本和来源文件哈希。
SHA256SUMS.json：本包内部文件哈希，不是发布者数字签名。

本包不读取远程网页，不包含账号密钥、字体文件或第三方依赖。请把下载文件保存在自己的备份位置。
'''
    example={'schema_id':'manju.model-request/v1','shot_id':'DEMO_01','task':'create',
      'prompt':'固定镜头拍摄一张纸随微风轻轻移动，保持背景、光线与构图一致。',
      'duration_s':None,'resolution':None,'aspect_ratio':None,'assets':[],
      'preserve':[],'change':[],'stage':'draft','approved_draft_sha256':None}
    payload={
      'OPEN_MODEL_WORKBENCH.html':html,
      'MODEL_CATALOG.json':(repo/'src/manju/authoring/data/catalog.json').read_bytes(),
      'EXAMPLE_REQUEST.json':(json.dumps(example,ensure_ascii=False,indent=2)+'\n').encode(),
      'READ_ME_FIRST_ZH.md':intro.replace('R7',stage).encode(),
      'VERSION.json':(json.dumps({'stage':stage,'version':version['version'],'git_head':head,
        'html_sha256':sha256(html).hexdigest(),'cumulative_features':version['features'],
        'full_native_application_included':False,'commercial_execution_connected':False,
        'original_r3_r4_bytes_recovered':False,'windows_double_click_certified':False},ensure_ascii=False,indent=2)+'\n').encode()}
    sums={name:sha256(data).hexdigest() for name,data in payload.items()}
    payload['SHA256SUMS.json']=(json.dumps(sums,indent=2)+'\n').encode()
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='manju-compact-') as td:
        temp=Path(td)/'toolkit.zip'
        with zipfile.ZipFile(temp,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
            for name,data in sorted(payload.items()):z.writestr(f'MANJU_{stage}_OFFLINE_TOOLS/'+name,data)
        with zipfile.ZipFile(temp) as z:
            if z.testzip() is not None:raise ValueError('Compact ZIP CRC failed')
            z.extractall(Path(td)/'check')
        for name,data in payload.items():
            if (Path(td)/f'check/MANJU_{stage}_OFFLINE_TOOLS'/name).read_bytes()!=data:raise ValueError('Extracted payload differs')
        with output.open('xb') as f:f.write(temp.read_bytes())
    return {'ok':True,'archive':str(output),'archive_sha256':sha256(output.read_bytes()).hexdigest(),
      'archive_bytes':output.stat().st_size,'members':len(payload),'git_head':head,
      'html_sha256':sha256(html).hexdigest(),'zip_crc_passed':True,'fresh_extraction_checked':True,
      'all_payload_bytes_match':True,'full_native_application_included':False,'client_download_confirmed':False}

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);p.add_argument('--repo',type=Path,default=Path(__file__).resolve().parents[2]);a=p.parse_args()
    print(json.dumps(build(a.repo.resolve(),a.output.resolve()),ensure_ascii=False,indent=2))
