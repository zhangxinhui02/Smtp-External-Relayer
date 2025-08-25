# 阿里云DirectMail中继适配器

~~本适配器通过调用阿里云DirectMail的API，动态增删发信地址，随后转发邮件，以满足大量发信地址的SMTP中继的需求。~~

**提示：由于阿里云DirectMail对于删除发信地址的操作有每月的数量限制，此中继适配器已经无法满足大量发信地址的需求。如有需求请考虑其他适配器。**

**对于静态发信地址的中继功能仍然可用。**

---

## 配置文件

此中继适配器的配置位于`config/config.yaml`文件的`aliyun_directmail`项下。

  - `access_key_id`: 鉴权Key ID。

  - `access_key_secret`: 鉴权Key Secret。

  - `smtp_ssl_encrypt`: 是否通过SSL加密协议来转发邮件。

  - `static_addresses_password`: 静态发信地址与SMTP密码的键值对。

    静态发信地址是在阿里云DirectMail控制台中配置好的发信地址，这些发信地址通常较为常用且不会被删除。

    示例配置：

    ```yaml
    static_addresses_password:
      alice@example.com: password_of_alice
      bob@example.com: password_of_bob
    ```

  - `mail_addresses_pool_count`: 工作地址池内最多同时处理的地址数量。

    阿里云DirectMail最多允许存在10个发信地址，因此并行发送邮件时，最多能同时创建10个地址并发送。此处填写10可以完全利用这10个地址，并发情况下提升发送效率。

    如果配置有静态地址，则需要降低此处的数值，避免极端情况下地址数量超过10造成错误。

    例如，如果配置了4个静态地址，那么此处最多可以填写`10 - 4 = 6`。

## 使用教程

1. 登录阿里云账号，进入[控制台](https://console.aliyun.com/)。搜索`邮件推送`产品并开通。[邮件推送](https://www.aliyun.com/product/directmail?accounttraceid=8247938e2b164877b381645b8b1e8558dtkx)

2. 进入[访问控制(RAM)页面](https://ram.console.aliyun.com/)，创建新RAM用户，勾选`使用永久AccessKey访问`，并记录AccessKeyID和AccessKeySecret。

3. 为RAM用户添加`AliyunDirectMailFullAccess`的权限。

4. 进入[邮件推送控制台](https://dm.console.aliyun.com/)，按要求配置发信域名。验证完成后将MX记录修改为实际的收信主机。

5. 如果需要配置静态发信地址，则在`发信地址`处按需添加，并配置SMTP密码。不需要配置回信地址。

6. 按实际情况填写配置文件并启动。
