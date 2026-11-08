# COM213 4.0 Final Project 调试笔记

2026-04 学期。以下记录来自 Raspberry Pi Pico H 智能停车检测原型的调试过程。

Session 2026-04. These notes come from debugging a smart-parking detection prototype built around a Raspberry Pi Pico H.

## 先确认本机输入与输出 | Verify local inputs and outputs first

逐个检查距离传感器、OLED、RGB LED 和蜂鸣器。先在 OLED 上观察距离读数和车位状态，确认输入变化能传到本机显示。逐个点亮 RGB 通道，并测试蜂鸣器在两个电平下的实际状态。这个原型的蜂鸣器为低电平触发，接线完成后实测一次可以避免报警逻辑反向。

Check the distance sensors, OLED, RGB LED and buzzer individually. Watch the distance reading and parking state on the OLED to confirm that an input change reaches the local display. Test each RGB channel separately and observe the buzzer at both logic levels. The buzzer on this prototype was active-low. Testing it after wiring helps verify which logic level activates it.

## 用串口终端定位上位机问题 | Isolate dashboard issues with a serial terminal

先用串口终端看状态消息是否完整、更新是否稳定，再启动上位机界面。如果本机状态和终端输出都在更新，可以优先检查上位机的解析和刷新。如果终端也没有新数据，就回到板端检查串口输出。

Inspect the status messages in a serial terminal before opening the dashboard. Check that complete messages arrive at a steady rate. If both the local status and terminal are updating, start with the dashboard parser and display refresh. If the terminal also stops receiving data, check the board's serial output.

## 分开记录循环耗时和刷新间隔 | Measure loop duration and refresh interval separately

在同一套硬件上比较两种运行方式时，分别记录主循环每轮耗时、单次传感器或 OLED 操作耗时，以及相邻串口状态帧的间隔。降低刷新频率可以缩短每轮循环耗时，同时增加下一条状态到达的等待时间。把这些指标并排记录，才能看清优化影响了哪部分。

When comparing two modes on the same hardware, record the loop duration, the duration of one sensor read or OLED refresh, and the interval between serial status frames. Refreshing less often can shorten a loop iteration while increasing the wait for the next update. Recording the metrics side by side shows where the trade-off occurs.
