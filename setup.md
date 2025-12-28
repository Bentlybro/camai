```
t=ultralytics/ultralytics:latest-jetson-jetpack6
sudo docker pull $t && sudo docker run -it --ipc=host --runtime=nvidia $t
```

```
yolo export model=yolo11n.pt format=engine
```


```
  sudo docker run -it --ipc=host --runtime=nvidia \
    --network=host \
    -v /home/bently/Desktop/camai:/app \
    -w /app \
    ultralytics/ultralytics:latest-jetson-jetpack6
```

```
sudo docker run -it --ipc=host --runtime=nvidia \
    --network=host \
    -v /home/bently/Desktop/camai:/app \
    -w /app \
    ultralytics/ultralytics:latest-jetson-jetpack6 \
    yolo export model=yolo11n.pt format=engine
```

```
yolo export model=yolo11n.pt format=engine
```

```
sudo docker run -it --ipc=host --runtime=nvidia \
    --network=host \
    -v /home/bently/Desktop/camai:/app \
    -w /app \
    ultralytics/ultralytics:latest-jetson-jetpack6 \
    yolo export model=yolo11n-pose.pt format=engine
```

```
yolo export model=yolo11n-pose.pt format=engine
```

```
sudo docker run -it --ipc=host --runtime=nvidia \
    --network=host \
    -v /home/bently/Desktop/camai:/app \
    -w /app \
    ultralytics/ultralytics:latest-jetson-jetpack6 \
    yolo export model=yolo11n-cls.pt format=engine half=True imgsz=224
```

```
yolo export model=yolo11n-cls.pt format=engine half=True imgsz=224
```

```
yolo export model=yolo11n.pt format=engine int8=True data=coco128.yaml
```

```
ls -la yolo11n.engine
```

```
python3 run.py
```