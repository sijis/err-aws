Err plugin for Amazon Web Services (AWS)
===

Requirements
---
```
pip install apache-libcloud
```

Installation
---
```
!repos install https://github.com/sijis/err-aws.git
```

Usage
---
Simple example usage

```
!aws create --ami=i-12321 --size=20 --tags="key1=val1,key2=val2" --keypair=my-key --instance_type=t2.medium app-server1
!aws reboot app-server1
```
