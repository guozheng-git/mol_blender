have the same functionality for pse render - commandline see test for pymol plugin
make it work on Macos too. apptainer won't work
remove legacy solutions
just have one format to define preset by json
have very broad presets that can render very diverse structures with multiple chains of diverse types of Protein, DNA, RNA, ligands etc. always show everything that is there
make a color follow mode where it takes information from pymol and take the pymol color to inspire the color in blender
make an option to save out blender session

##how the plugin should work
take structure as seen in pymol and send it off to blender in the apptainer including the camera angle and then return a rendered png show in pymol. the command should be blender_render inside pymol with arguments specifying different things
it should be installable by just adding run /home/magnusb/blender_bridge/pymol_plugin_bridge/pymol_bridge_plugin.py into the pymolrc
there should be an easy blender_render command that just runs with default preset1 without arguments that just runs locally
make sure that all the variables that are avaiable in the preset actually are used in blender and make sure they are connected correctly

##interactive GPU session
make an open interactive GPU session where the apptainer can be also executed and returns a png
the mode should use the same call as a local call
there should not be an extra script if possible

##Git
make a commit once a project is finished not just when one round is over

##example style
an example for preset 1 style is given here: /home/magnusb/blender_bridge/pse_to_png_onefile_standalone.py
this is just for reference how the style should look in the end since here we can not reproduce it for some reason yet

##test for pymol plugin
pymol -cq ./CD3.pse -d "run /home/magnusb/blender_bridge/pymol_plugin_bridge/pymol_bridge_plugin.py; blender_render selection='(all)', style_json='/home/magnusb/blender_bridge/pymol_plugin_bridge/style_preset_1_opaque_pastel.json',mode='local', use_apptainer=1, apptainer_image='/home/magnusb/blender_bridge/pymol_plugin_bridge/blender_bridge.sif',output_dir='.', output_name='render_exact5.png', rough=0; quit"

probably a separate commandline argument is needed if it can be done like that its cleaner so keep this as commandline execute