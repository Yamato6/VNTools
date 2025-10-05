
# FS Engine

## CrossChannel_MultiTool:

``CrossChannel_MultiTool.exe extract archive.pd output_folder
  CrossChannel_MultiTool.exe pack output_folder output.pd PackOnly / PackPlus``

# FVP

## BINTool

``java -jar BINTool.jar -d input.bin output_folder  
  java -jar BINTool.jar -c input_folder output.bin``

## NVSGTool

``java -jar NVSGTool.jar --decode input.nvsg output.png
  java -jar NVSGTool.jar --encode input.png output.nvsg --x 100 --y 200 --image_count 1``
