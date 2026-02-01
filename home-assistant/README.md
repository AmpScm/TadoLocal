To install this as a Home Assistant add-on, you need to do the following:

- SSH or SFTP into your HA filesystem.
- Under the /addons directory, create a directory named tado-local
- Copy alle the files from the home-assistant folder in this project under the 'tado-local' directory

Then follow the steps that are described on https://developers.home-assistant.io/docs/add-ons/tutorial under 'Installing and testing your add-on'.

Before starting, set the IP-address and pincode of the Tado Bridge. See the generic documentation of TadoLocal for more information.

When you start the addon for the first time, check the logs. The logs will display an URL that you need to authenticatie at Tado.

When you have successfully authenticated at Tado, check the logs again. It will confirm the authentication and that the TadoLocal services will start running.
