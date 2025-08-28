# Microsoft Exchange Online 中继适配器

本适配器通过调用Microsoft Graph API和Microsoft Exchange Online Powershell Management，动态为发信人地址新建共享邮箱(共享邮箱无硬性上限)，随后通过特定用户转发邮件，以满足大量发信地址的SMTP中继的需求。

---

## 配置文件

此中继适配器的配置位于`config/config.yaml`文件的`microsoft_exchange_online`项下。

  - `organization`: 你的组织的域，例如`example.onmicrosoft.com`。

  - `tenant_id`: 你的组织的租户ID。

  - `client_id`: 应用程序的Client ID。

  - `client_secret`: 应用程序的Client Secret。

  - `sender`: 组织中用于实际代理发信的用户(邮箱)。

  - `certificate_path`: 应用程序的鉴权证书路径(pfx格式)，指定certificate_b64时可以忽略。certificate_path与certificate_b64必须至少提供一个。

  - `certificate_b64`: base64编码的证书。certificate_path与certificate_b64必须至少提供一个。

  - `certificate_password`: 应用程序的鉴权证书的密码。如果没有密码可以设置为空字符串。

  - `powershell_cmd`: 操作系统中的Powershell命令。通常Linux系统为`pwsh`，Windows系统为`powershell`。

## 使用教程

使用此中继适配器需要较复杂的配置，并且需要具有Microsoft Exchange Online的订阅(如Microsoft 365 E5开发者计划)。

Microsoft 365和Microsoft Exchange Online在更新某些配置的时候非常缓慢，极端情况下会超过一小时。如果在配置时遇到意料之外的结果，可以等待一段时间再试。

1. 如果你的组织通过Microsoft 365分配许可证，登录[Microsoft 365 admin center](https://admin.microsoft.com/)，新建或选择一名用户，为其分配Exchange Online许可证。此用户将成为实际代理发信的用户，此用户的主邮箱地址即为`sender`，记录下此信息。

2. 为你的组织添加自己的域名。注意正确设置邮件相关解析，除了MX记录。将MX记录解析到实际的收信主机，而不是Microsoft Exchange Online的主机。

3. 登录[Microsoft Entra](https://entra.microsoft.com/)，在主页可以看到你的组织的租户ID(`tenant_id`)和主域(`organization`)，记录下这些信息。

4. 点击`Entra ID` -> `应用注册` -> `新注册`，为应用起一个名称(如`SMTP-External-Relayer`)，其他项保持默认，点击`注册`。此应用用于管理Exchange Online的共享邮箱和调用发信接口。

5. 在应用详情页的`概述` -> `摘要`处，可以得到应用的应用程序(客户端)ID(`client_id`)。记录下此信息。

6. 在应用详情页，点击`API权限` -> `添加权限` -> `Microsoft API` -> `Microsoft Graph` -> `应用程序权限`，然后添加`Mail.Send`和`User.Read.All`权限。

7. 继续添加权限，点击`添加权限` -> `我的组织使用的API` -> `Office 365 Exchange Online` -> `应用程序权限`，然后添加`Exchange.ManageAsApp`权限。

8. 点击同意按钮，代表你的组织授予管理员同意，即可为应用授权上述权限。

9. 在应用详情页，点击`证书和密码` -> `客户端密码` -> `新客户端密码`，新建一个客户端密码，截止期限最大可以选择2年(这意味着此密码最少需要每2年轮换一次)。记录下密码的`值`，这就是`client_secret`。离开此页面将不会再显示此值。

10. 打开终端，运行以下命令来生成私钥和自签证书，用于管理Exchange Online时的鉴权。妥善保存这些证书并记录路径。

    ```shell
    # 生成私钥和自签证书（RSA 2048） 注意730天后需要轮换
    openssl req -x509 -newkey rsa:2048 -keyout smtp-external-relayer.key -out smtp-external-relayer.cer -days 730 -nodes -subj "/CN=smtp-external-relayer"
    # 打包成 PFX（可设置一个强口令，也可以留空）
    openssl pkcs12 -export -out smtp-external-relayer.pfx -inkey smtp-external-relayer.key -in smtp-external-relayer.cer
    ```

11. 回到Microsoft Entra的应用详情页，点击`证书和密码` -> `证书` -> `上传证书`，上传上一步生成的证书公钥`smtp-external-relayer.cer`。

12. 打开Microsoft Entra的`企业应用`，在所有应用程序中找到上面创建的应用程序，并点击进入。在`概述`中可以看到此应用程序的`对象ID`，记录下来。

13. 在具有GUI的环境中打开Powershell，运行以下命令安装Exchange管理工具，并以管理员账户连接你的组织(在弹出的窗口中登录你的组织)。

    ```powershell
    # 安装Exchange管理工具
    Install-Module -Name ExchangeOnlineManagement
    
    # 连接你的组织的Exchange
    Connect-ExchangeOnline
    # 如果你管理多个租户，可以指定
    # Connect-ExchangeOnline -UserPrincipalName user@yourdomain.com -Organization yourtenant.onmicrosoft.com
    ```

14. 依次运行以下命令，配置组织和应用权限。

    ```powershell
    # 启用别名发信功能
    Set-OrganizationConfig -SendFromAliasEnabled $true

    # 创建服务主体指针，需要填写AppId(应用程序的client_id)、ObjectId(应用程序的`对象ID`)和名称(例如`SMTP-External-Relayer`)
    New-ServicePrincipal -AppId "<AppId>" -ObjectId "<ObjectId>" -DisplayName "SMTP-External-Relayer"
    
    # 为应用分配“Mail Recipient Creation”角色
    New-ManagementRoleAssignment -App "<ObjectId>" -Role "Mail Recipient Creation"
    
    # 为应用分配“Mail Recipients”角色
    New-ManagementRoleAssignment -App "<ObjectId>" -Role "Mail Recipients"
    ```

15. 配置完毕，断开连接。

    ```powershell
    Disconnect-ExchangeOnline
    ```

16. 将上述记录的信息填写到配置文件中，启动项目。

## 注意事项

- 由于Microsoft Exchange Online的管理生效非常缓慢，因此新发信地址第一次发邮件的耗时可能会达到30秒以上。后续发信即可大幅改善。

- 新用户发信时，如果组织中已经存在同名的电子邮件地址，可能会发生错误。因为本中继适配器对于已有的电子邮件地址不会进行初始化，而初始化是为了添加必要的`SendAs`权限以允许代理人利用此地址发信，因此会无发送权限。
