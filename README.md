# Docker From Scratch Workshop

This is my code for studying [Docker From Scratch Workshop](https://github.com/Fewbytes/rubber-docker).

## Environment setup

Actually, the original code has provided the `Vagrantfile` for
setting up the environment. However, due to the GFW, things suck.
So I decide to use qemu.

First, download the 16.04 LTS ubuntu from tuna.

```sh
wget https://mirrors.tuna.tsinghua.edu.cn/ubuntu-releases/xenial/ubuntu-16.04.7-server-amd64.iso
```

Next, you should configure the proxy. And execute the following command.

```sh
sudo bash packer/bootstrap.sh
```

---

This workshop may seem easy. However, it provides a basic understanding how
docker works internal. Thanks all the efforts of the authors.
