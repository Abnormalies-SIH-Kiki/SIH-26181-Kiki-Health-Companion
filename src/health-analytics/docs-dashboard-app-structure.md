# SIH Dashboard APP Structure

First when user has first time entered the app ( only the first time , when you have no login information about the user) , he should go through these four cards 

First - the card of onboarding_personal_details_2

Second - onboarding_location_2

Third - onboarding_daily_goals_1

After details - there should be buffer time in setting up the user details and storing it back (You can use SQLite database for it , or any other database if you need to store user details)

After that - 

Premium_dashboard should appear as according to user and their data you have at that point for now , adjust everything accordingly. The API’s , user’s data collected from sensor etc. will be given later.

at the bottom we have nav bar for going to different pages , and also on the main page we have options for going to different pages . Those different pages are of Weather and heat index page - For this just build the UI , and frontend , it will function well , once it’s API will be given , which is not right now . And then the sleep pattern . Make sure the options for these pages in the nav bar and the their cards on the main dashboard , redirect the user to their official pages. 

There are two other pages as well , one is High heart rate health page , and low heart rate health page , For this - It will be shown - First : in the profile page log button ( which will be told later) and second it will show when the card made will show high heart rate , then their will be a button popping up at that time which will direct it to high heart rate , and similarly when the card will be showing low heart rate , and button will pop up at the same place for the low heart rate page.
This high heart and low heart rate will pop up with the help of a machine learning rule based model , which will be prepared later , since it is a backend , and your job is just for frontend , so dont go that way , just keep space for it to merge later and make sure the front end is not making conflict with it.

Last - The profile page , make it as it is and in sync with the database given by the user to app.
All the details of the profile to be shown here , the log buttons have the function here , the fall detection page to be displaced and user to be redirected to that page , once user clicks the fall detection option .
On Clicking the heart rate alerts log button user will be redirected to another log button page with same Ui and with now two different log button options, one for high heart rate alert and one for low heart rate alert , and then those pages of high heart rate will be displaced when user will click the high heart rate option and low heart rate when user will click the low heart rate option.

iN THE EDIT PROFILE AND EDIT GOALS detail , user would be redirected to the same input cards page which was shown at the beginning to edit the details and the edited details will be updated in the database with all changes synced on the particular pages that are using those data.

For now do these. Make sure there is no disturbance in the design , and app would be functioning well on the frontend side and the data side . Make it perfect.