# 角色美术与许可

鲸鲸优先使用本机安装的 Aboutme 大肥鱼动画。未安装时使用角色包自带的自绘鲸娘，优先使用骨骼形象，也可显示立绘。两套素材都不可用时才显示占位形象。Aboutme 的上游美术禁止商用，公开二创须署名 PC2005-cloud/dsh-pet。安装步骤和上游声明见 [Aboutme 素材说明](../docs/aboutme-pet.md)。

项目原创美术中，自绘鲸娘的[运行骨骼及纹理](../src/aicompanion/characters/deepseek-whale/rig/)和[立绘](../src/aicompanion/characters/deepseek-whale/sprites/)、[应用图标](../src/aicompanion/assets/icons/)及[六张聊天表情](../src/aicompanion/assets/expression/)按根目录的 [MIT License](../LICENSE) 发布。公司名称和徽标仍是各自权利人的商标。

## 从 Git 历史找回分层源稿

自绘鲸娘的分层源稿和制作预览保存在提交 `fa54555`。在仓库根目录运行以下命令，可查看当时的源包说明，或将源稿导出为本机 ZIP 文件。

```console
git show fa54555:art/deepseek-whale/README.md
git archive --format=zip --output=whale-art-fa54555.zip fa54555 art/deepseek-whale
```

制作自己的角色时，可以用 `tools/rig_studio.py` 标注和编译分层源图，用 `tools/build_character_pack.py` 将自己的十姿态立绘编译为角色包。
