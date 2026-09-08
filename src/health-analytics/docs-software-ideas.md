# SIH Ideas for Software Part

## Ideas regarding databases (SQLite) -

- Basically Adding a feature every 15 minutes recording , like total 96 readings in a day (24*4) is a great idea.
- We will take inputs from the user about his ongoing life style , like how many hours does he sleep and between what to what time , how much does he exercise ( like 2-3 times per week , or something related) for better output and readings. - **We can basically ask these on mobile interface , while setting it up , and then update in our database and can make our model act accordingly.**

## Overall Project Structure -

```markdown
HeartRate_AI_Project/
│
├── esp32_firmware/ 
│   └── esp32_firmware.ino       # 1. ESP32 Code (Reads sensor & sends JSON) (C++)
│
├── database.py                  # 2. Database manager (Creates table, inserts & fetches data)
├── data_collector.py            # 3. Listens to ESP32 and stores readings in SQLite
├── model_inference.py           # 4. Loads Deep Learning model & runs predictions on SQLite data
└── main.py                      # 5. Master runner (Runs collector + Real-time AI prediction)
```