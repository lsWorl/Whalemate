"""在表达层合成新动作与旧版回退；不更换角色、人格或关系。"""
from dataclasses import replace
from aicompanion.characters.animation_pack import AnimationPack


def with_reference_actions(original,reference):
    if original.size!=reference.size:raise ValueError('新旧动画画布必须一致。')
    # 旧版头顶约 8.7%、脚底92.2%；等比缩到新规格的18% / 85%。
    def framed(clip):return replace(clip,content_scale=.8,content_offset=(.1,.1124))
    actions={key:framed(clip) for key,clip in original.actions.items()}
    actions.update(reference.actions)
    aliases={'walking':'walking_right','wave':'click_wave','shy':'shy_peek','pout':'pout_stomp','patted':'happy_hops','thinking':'curious','poked':'click_wave'}
    for key,target in aliases.items():actions[key]=replace(actions[target],id=key)
    poses={**original.poses,**reference.poses}
    for key,target in aliases.items():poses[key]=target
    # 新动作首尾均为统一站姿，直接衔接；旧大姿势仍保留原绘制片段。
    replaced=set(aliases)|set(reference.actions)
    transitions={pair:replace(item,clip=framed(item.clip)) for pair,item in original.transitions.items()
                 if pair[0] not in replaced-{'idle'} and pair[1] not in replaced-{'idle'}}
    return AnimationPack(original.size,actions,poses,reference.poster,reference.head,transitions)
