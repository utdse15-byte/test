"""Read-only bridge from an existing shot to portable authoring input."""
from __future__ import annotations
from pathlib import Path
from .core import Asset, Request, AuthoringError, digest, file_digest
from ..core.container import Project
from ..providers.video_authoring import build_video_authoring_plan


def from_shot(project_path: Path, shot_id: str) -> dict:
    project = Project(project_path)
    original = build_video_authoring_plan(project, shot_id)
    facts = original.to_dict()
    assets, warnings = [], []
    for frame in original.keyframes:
        role = {'start': 'first_frame', 'end': 'last_frame'}.get(frame.role)
        if not role or not frame.local_asset or not frame.source_path or not frame.sha256:
            warnings.append(f'关键帧 {frame.id} 未绑定本地首/尾帧，请人工处理；没有自动下载远程素材。')
            continue
        assets.append(Asset(id=frame.id, role=role, path=frame.local_asset,
                            sha256=frame.sha256.removeprefix('sha256:'),
                            bytes=frame.source_path.stat().st_size))
    for physical in original.reference_graph.physical:
        role = {'image': 'subject_reference', 'video': 'reference_video', 'audio': 'reference_audio'}.get(physical.kind)
        if not role or not physical.local_asset or not physical.source_path or not physical.sha256:
            warnings.append(f'参考素材 {physical.id} 未绑定本地文件，请人工处理；没有自动下载远程素材。')
            continue
        assets.append(Asset(id=physical.id, role=role, path=physical.local_asset,
                            sha256=physical.sha256.removeprefix('sha256:'),
                            bytes=physical.source_path.stat().st_size))
    counts = {a.role for a in assets}
    task = ('bridge' if {'first_frame', 'last_frame'} <= counts else
            'animate' if counts & {'first_frame', 'last_frame'} else
            'reference' if assets else 'create')
    ms = original.duration_ms
    seconds = ms // 1000 if ms is not None and ms % 1000 == 0 and 0 < ms <= 120000 else None
    if ms is not None and seconds is None:
        warnings.append(f'原镜头时长 {ms}ms 不是该参数范围内的整秒。生成时长留空，请按供应商选择；未修改原镜头。')
    request = Request(shot_id=original.shot_id, task=task,
                      prompt=original.exact_prompt_override if original.exact_prompt_override is not None else original.compiled_prompt,
                      duration_s=seconds, aspect_ratio=original.aspect_ratio, assets=assets)
    return {'schema_id': 'manju.project-authoring-import/v1', 'request': request.model_dump(mode='json'),
            'source_plan': facts, 'source_plan_sha256': digest(facts), 'warnings': warnings,
            'project_modified': False,
            'note': '源计划保留角色/控制绑定语义；新请求中的角色初值仍需人工核对。视频时长没有自动探测。'}
