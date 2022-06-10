# About

This little Docker container will connect to [APRS-IS](http://aprs-is.net/) over the internet and copy APRS position reports into device locations in a [Traccar](https://www.traccar.org/) server.  
In a multi-user environment it allows users to configure devices without involving the administrator.  
Forked from [PhilRW/aprs2traccar](https://github.com/PhilRW/aprs2traccar)
## How to

### Docker

Clone this repo and then add this to your `docker-compose.yml` file:

```yaml
  aprs2traccar:
    build: https://github.com/itec78/aprs2traccar.git
    container_name: aprs2traccar  # optional
    environment:
      - "APRS_CALLSIGN=FO0BAR"
      - "APRS_HOST=euro.aprs2.net"  # optional but recommended, defaults to rotate.aprs.net
      - "TRACCAR_HOST=https://traccar.example.com"  # optional, defaults to http://traccar:8082
      - "TRACCAR_USER=user" # optional but recommended
      - "TRACCAR_PASSWORD=pass" # optional but recommended
      - "TRACCAR_KEYWORD=aprs_in" # optional, defaults to aprs
      - "TRACCAR_INTERVAL=120" # optional, defaults to 60
      - "LOG_LEVEL=DEBUG"  # optional, defaults to INFO
    restart: unless-stopped
  ```
  
  * `APRS_CALLSIGN` is your callsign and what you use to connect to APRS-IS.
  * `APRS_HOST` is the APRS-IS host to connect to.
  * `TRACCAR_HOST` is your Traccar server's URI/URL. If run in the same docker-compose stack, name your Traccar service `traccar` and omit this env var.
  * `TRACCAR_USER` is your Traccar server's username. It should be the admin or an admin user with readonly permission.
  * `TRACCAR_PASSWORD` is your Traccar server's password.
  * `TRACCAR_KEYWORD` is the attribute name to be set in your device. The APRS callsign you want to import
  * `TRACCAR_INTERVAL` is the polling time (in seconds) of the traccar devices.



### Traccar

Create a device with arbitrary identifier.  
Add a device attribute with Name = `TRACCAR_KEYWORD` and value = callsign you intend to track.  
Wait `TRACCAR_INTERVAL` seconds in order for the changes takes effect.  

