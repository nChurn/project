import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from typing import Optional

from aiogram import Bot, Dispatcher, Router, types, F
from aiogram.client.session import aiohttp
from aiogram.dispatcher.filters.command import CommandObject
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher.fsm.context import FSMContext
from aiogram.types import PhotoSize

import texts
from api.articles.routers import new_article
from api.articles.schemas import ArticleCreate
from api.contragents.routers import create_contragent
from api.contragents.schemas import ContragentCreate
from api.payments.routers import create_payment
from api.payments.schemas import PaymentCreate
from api.pboxes.routers import create_paybox
from api.pboxes.schemas import PayboxesCreate
from const import DEMO, PAY_LINK, cheque_service_url
from database.db import database, cboxes, users, users_cboxes_relation, accounts_balances, tariffs, pboxes, payments, \
    articles, cheques, contragents, messages
from functions.cboxes import create_cbox, join_cbox
from functions.helpers import gen_token
from producer import produce_message

logging.basicConfig(level=logging.INFO)
bot = Bot(os.environ.get("TG_TOKEN"), parse_mode="HTML")

app_url = os.environ.get("APP_URL")

router_comm = Router()
router_add_migrate = Router()


class Form(StatesGroup):
    start = State()
    join = State()
    cheque_paybox = State()
    cheque_picture = State()


class DeliveryForm(StatesGroup):
    send = State()
    submit = State()


@dataclass
class BroadcastMessageStore:
    picture: str
    text: str
    tg_message_id: int
    tg_user_or_chat: str
    created_at: str


cancel_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[[types.KeyboardButton(text="Отмена")]],
    resize_keyboard=True,
    one_time_keyboard=True,
)

choose_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[[types.KeyboardButton(text="Yes")], [types.KeyboardButton(text="No")]],
    resize_keyboard=True,
    one_time_keyboard=True,
)


async def store_user_message(message: types.Message):
    relship = messages.insert().values(
        tg_message_id=message.message_id,
        tg_user_or_chat=str(message.chat.id),
        from_or_to=str(message.from_user.id),
        created_at=str(datetime.now()),
        body=message.text if message.text else "photo"
    )
    await database.execute(relship)


async def store_bot_message(tg_message_id: int, tg_user_or_chat: str, from_or_to: str, body: str):
    relship = messages.insert().values(
        tg_message_id=tg_message_id,
        tg_user_or_chat=str(tg_user_or_chat),
        from_or_to=str(from_or_to),
        created_at=str(datetime.now()),
        body=body
    )
    await database.execute(relship)


SHARE_NUMBER_KEYBOARD = types.ReplyKeyboardMarkup(keyboard=[
    [
        types.KeyboardButton(text="Отправить номер", request_contact=True),
        types.KeyboardButton(text="Отменить регистрацию")
    ]
], row_width=1, resize_keyboard=True)


def get_open_app_link(token: str) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[
            [
                types.InlineKeyboardButton(
                    text="🌍 Открыть приложение",
                    url=texts.app_url_with_token.format(base_url=app_url, token=token)
                ),
            ],
            [
                types.InlineKeyboardButton(
                    text="💰 Оплатить подписку",
                    url=PAY_LINK
                )
            ],
        ]
    )


async def welcome_and_share_number(message: types.Message):
    await message.answer(text=texts.welcome.format(username=message.from_user.username))
    await store_bot_message(
        tg_message_id=message.message_id + 1,
        tg_user_or_chat=str(message.chat.id),
        from_or_to=str(bot.id),
        body=texts.welcome.format(username=message.from_user.username)
    )
    await message.answer(text=texts.complete_register, reply_markup=SHARE_NUMBER_KEYBOARD)
    await store_bot_message(
        tg_message_id=message.message_id + 1,
        tg_user_or_chat=str(message.chat.id),
        from_or_to=str(bot.id),
        body=texts.complete_register
    )


async def user_rel_to_cashbox(user_id: int, cbox_id: int):
    query = users_cboxes_relation.select().where(
        users_cboxes_relation.c.cashbox_id == cbox_id,
        users_cboxes_relation.c.user == user_id
    )
    return await database.fetch_one(query)


async def create_user_to_cashbox_rel(user_id: int, cbox_id: int):
    rel_token = gen_token()
    relship = users_cboxes_relation.insert().values(
        user=user_id, cashbox_id=cbox_id, token=rel_token,
        status=True, is_owner=False,
        created_at=int(datetime.utcnow().timestamp()),
        updated_at=int(datetime.utcnow().timestamp())
    )

    rl_id = await database.execute(relship)

    return await database.fetch_one(
        users_cboxes_relation.select().where(
            users_cboxes_relation.c.id == rl_id
        )
    )


async def generate_new_user_access_token_for_cashbox(user_id: int, cashbox_id: int) -> str:
    token = gen_token()
    query = users_cboxes_relation.update().where(
        users_cboxes_relation.c.cashbox_id == cashbox_id,
        users_cboxes_relation.c.user == user_id
    ).values(
        {"token": token}
    )
    await database.execute(query)
    return token


@router_comm.message(F.chat.type == "private", commands="start")
async def cmd_start(message: types.Message, state: FSMContext, command: CommandObject):
    """
    /start command handler for private chats
    :param message: Telegram message with "/start" command
    """
    await store_user_message(message)

    user = await database.fetch_one(
        users.select().where(
            users.c.chat_id == str(message.chat.id),
            users.c.owner_id == str(message.from_user.id)
        )
    )

    invite_token = command.args

    if invite_token:
        # Если пользователь пришел по пригласительной ссылке -
        # пытаемся достать cashbox к которому его хотят присоединить
        cbox_by_invite = await database.fetch_one(cboxes.select().where(cboxes.c.invite_token == invite_token))

        if not cbox_by_invite:
            # Если пригласительный токен - невалидный - возвращаем ошибку
            answer = texts.bad_token
            await message.answer(text=answer, reply_markup=types.ReplyKeyboardRemove())
            await store_bot_message(
                tg_message_id=message.message_id + 1,
                tg_user_or_chat=str(message.chat.id),
                from_or_to=str(bot.id),
                body=answer
            )
            return

        if user:
            # Если юзер уже существовал - проверяем привязку к cashbox в который его пригласили.
            # Если привязки нет - создаем новую.
            user_to_cashbox_rel = await user_rel_to_cashbox(user_id=user.id, cbox_id=cbox_by_invite.id)
            if not user_to_cashbox_rel:
                user_to_cashbox_rel = await create_user_to_cashbox_rel(user_id=user.id, cbox_id=cbox_by_invite.id)

            answer = texts.invite_cbox.format(token=user_to_cashbox_rel.token, url=app_url)
            await message.answer(text=answer, reply_markup=get_open_app_link(user_to_cashbox_rel.token))
            await store_bot_message(
                tg_message_id=message.message_id + 1,
                tg_user_or_chat=str(message.chat.id),
                from_or_to=str(bot.id),
                body=answer
            )
        else:
            # Иначе приглашаем юзера присоединиться в систему и сохраняем информацию о приглашении
            await welcome_and_share_number(message)
            await state.set_state(Form.join)
            await state.update_data(cbox=dict(cbox_by_invite))
        return

    if not user:
        # В случае если юзера не приглашали и он ещё не был зарегистрирован - приглашаем и выходим
        await welcome_and_share_number(message)
        await state.set_state(Form.start)
        return

    users_cbox = await database.fetch_one(cboxes.select().where(cboxes.c.admin == user.id))

    if users_cbox:
        # Если у юзера есть cashbox - генерируем новый токен для него
        token = await generate_new_user_access_token_for_cashbox(user_id=user.id, cashbox_id=users_cbox.id)
        answer = texts.change_token.format(token=token, url=app_url)
    else:
        # В ином случае создаем новый cashbox
        token = (await create_cbox(user)).token
        answer = texts.create_cbox.format(token=token, url=app_url)

    # Возвращаем ссылку на открытие приложения
    await message.answer(text=answer, reply_markup=get_open_app_link(token))
    await store_bot_message(
        tg_message_id=message.message_id + 1,
        tg_user_or_chat=str(message.chat.id),
        from_or_to=str(bot.id),
        body=answer
    )


@router_comm.message(commands="newcheque")
async def new_cheque(message: types.Message, state: FSMContext):
    """
    /newcheque command handler
    :param message: Telegram message instance
    """
    await store_user_message(message)
    await state.set_state(Form.cheque_picture)
    await message.answer(texts.send_cheque, reply_markup=cancel_keyboard)
    await store_bot_message(message.message_id + 1, message.chat.id, bot.id, texts.send_cheque)


async def get_cheque_info(photo: PhotoSize) -> Optional[dict]:
    file = BytesIO()
    await bot.download(photo.file_id, file)
    file.seek(0)
    async with aiohttp.ClientSession() as session:
        req_data = {
            "token": os.getenv("CHEQUES_TOKEN"),
            "qrfile": file,
        }
        async with session.post(
                cheque_service_url,
                data=req_data,
        ) as resp:
            data = await resp.json()
            if data["code"] != 1:
                return None
            else:
                return data["data"]["json"]


def get_cheque_items_text(cheque_info):
    description = ""
    for item in cheque_info["items"]:
        description = description + f'{item["name"]} ({item["quantity"]} шт.) = {item["sum"] / 100} р.\n'
    return description.strip()


async def create_payment_from_cheque(cheque_info: dict, cbox) -> int:
    """Creates payment with the provided data and returns its id"""
    created = int(datetime.utcnow().timestamp())

    # getting user token
    query = users_cboxes_relation.select().where(users_cboxes_relation.c.user == cbox.admin)
    user_info = await database.fetch_one(query)
    token = user_info.token

    # getting id of the article called "Покупки"
    query = articles.select().where(articles.c.cashbox == cbox.id, articles.c.name == "Покупки")
    article = await database.fetch_one(query)
    if not article:
        # creating article if it doesn't exist
        article_data = ArticleCreate(
            name="Покупки",
            emoji="🛍️",
        )

        article = await new_article(token, article_data)

    # saving cheque to db
    cheque_values = {
        "data": cheque_info,
        "created_at": created,
        "cashbox": cbox.id,
        "user": cbox.admin,
    }
    query = cheques.insert().values(cheque_values)
    cheque_id = await database.execute(query)

    # getting cheque items for the payment description
    description = get_cheque_items_text(cheque_info)

    # getting contragent
    query = contragents.select().where(contragents.c.inn == cheque_info["userInn"],
                                       contragents.c.cashbox == cbox.id)
    contragent = await database.fetch_one(query)
    if not contragent:
        contragent_data = ContragentCreate(
            name=cheque_info["user"],
            inn=cheque_info["userInn"],
        )
        contragent = await create_contragent(token, contragent_data)

    # setting temporary paybox
    query = pboxes.select().where(pboxes.c.cashbox == cbox.id)
    pbox = await database.fetch_one(query)
    if not pbox:
        paybox_data = PayboxesCreate(
            name="Наличные",
            start_balance=0.0,
        )
        pbox = await create_paybox(token, paybox_data)

    # creating payment
    payment_data = PaymentCreate(
        type="outgoing",
        name=f"Новый чек №{cheque_info['fiscalDocumentNumber']}",
        article="Покупки",
        article_id=article.id,
        amount=cheque_info["totalSum"] / 100,
        amount_without_tax=cheque_info["totalSum"] / 100,
        description=description,
        date=int(datetime.strptime(cheque_info["dateTime"], "%Y-%m-%dT%H:%M:%S").timestamp()),
        contragent=contragent.id,
        cashbox=cbox.id,
        paybox=pbox.id,
        account=cbox.admin,
        cheque=cheque_id,
        status=True,
        stopped=False,
        created_at=created,
        updated_at=created,
    )
    payment = await create_payment(token, payment_data)
    return payment["id"]


@router_comm.message(content_types=["photo"], state=Form.cheque_picture)
async def new_cheque_pic(message: types.Message, state: FSMContext):
    """
    New cheque picture
    :param message: Telegram message instance
    """
    await store_user_message(message)
    query = users.select().where(users.c.chat_id == str(message.chat.id))
    user = await database.fetch_one(query)

    if user:
        query = cboxes.select().where(cboxes.c.admin == user.id)
        cbox = await database.fetch_one(query)

        if cbox:
            cheque_info = await get_cheque_info(message.photo[-1])
            if not cheque_info:
                await message.answer(
                    texts.cheque_not_detected,
                    reply_markup=cancel_keyboard,
                )
                await store_bot_message(message.message_id + 1, message.chat.id, bot.id, texts.cheque_not_detected)
                return
            payment = await create_payment_from_cheque(cheque_info, cbox)
            await state.set_state(Form.cheque_paybox)
            await state.set_data(
                {"payment": payment, "cheque": cheque_info}
            )
            query = pboxes.select().where(pboxes.c.cashbox == cbox.id)
            payboxes = await database.fetch_all(query)
            buttons = []
            for paybox in payboxes:
                if buttons and len(buttons[-1]) < 2:
                    buttons[-1].append(types.InlineKeyboardButton(text=paybox.name, callback_data=paybox.id))
                else:
                    buttons.append([types.InlineKeyboardButton(text=paybox.name, callback_data=paybox.id)])
            keyboard = types.InlineKeyboardMarkup(inline_keyboard=buttons)
            await message.answer(
                texts.select_paybox,
                reply_markup=keyboard,
            )
            await store_bot_message(message.message_id + 1, message.chat.id, bot.id, texts.select_paybox)


@router_comm.callback_query(state=Form.cheque_paybox)
async def select_cheque_paybox(callback_query: types.CallbackQuery, state: FSMContext):
    message = callback_query.message
    query = users.select().where(users.c.chat_id == str(message.chat.id))
    user = await database.fetch_one(query)

    if user:
        query = cboxes.select().where(cboxes.c.admin == user.id)
        cbox = await database.fetch_one(query)

        if cbox:
            query = pboxes.select().where(pboxes.c.cashbox == cbox.id, pboxes.c.id == int(callback_query.data))
            pbox = await database.fetch_one(query)
            if not pbox:
                await callback_query.answer("Ошибка! " + texts.select_paybox)
                return
            state_data = await state.get_data()
            payment_id = state_data["payment"]
            cheque = state_data["cheque"]
            query = payments.update().where(payments.c.id == payment_id) \
                .values(paybox=pbox.id)
            await database.execute(query)
            await state.clear()
            await callback_query.answer("Готово!")
            await message.answer(
                texts.you_added_cheque.format(
                    amount=cheque["totalSum"] / 100,
                    address=cheque["retailPlaceAddress"],
                    items=get_cheque_items_text(cheque),
                    paybox=pbox.name,
                ),
                reply_markup=types.ReplyKeyboardRemove(),
            )
            await store_bot_message(message.message_id + 1, message.chat.id, bot.id, texts.you_added_cheque.format(
                amount=cheque["totalSum"] / 100,
                address=cheque["retailPlaceAddress"],
                items=get_cheque_items_text(cheque),
                paybox=pbox.name,
            ))
            await message.delete()


@router_comm.message(F.chat.type.in_({"group", "supergroup"}), commands="start")
async def cmd_id_groups(message: types.Message, state: FSMContext):
    """
    /start command handler for (super)groups
    :param message: Telegram message with "/start" command
    """
    await store_user_message(message)
    admins = await bot.get_chat_administrators(message.chat.id)
    creator = [i.user for i in admins if i.status == "creator"][0]

    if message.from_user.id == creator.id:
        query = users.select().where(users.c.owner_id == str(creator.id))
        user = await database.fetch_one(query)

        if user:
            query = users.select().where(users.c.owner_id == str(creator.id), users.c.chat_id == str(message.chat.id))
            user_and_chat = await database.fetch_one(query)

            if user_and_chat:
                query = cboxes.select().where(cboxes.c.admin == user_and_chat.id)
                cbox = await database.fetch_one(query)

                if cbox:
                    new_token = gen_token()
                    query = users_cboxes_relation.update().where(users_cboxes_relation.c.cashbox_id == cbox.id,
                                                                 users_cboxes_relation.c.user == user_and_chat.id).values(
                        {"token": new_token})

                    await database.execute(query)
                    answer = texts.change_token.format(token=new_token, url=app_url)
                    await message.answer(text=answer, reply_markup=get_open_app_link(new_token))
                    await store_bot_message(message.message_id + 1, message.chat.id, bot.id, answer)
                else:
                    rel = await create_cbox(user_and_chat)
                    answer = texts.create_cbox.format(token=rel.token, url=app_url)
                    await message.answer(text=answer, reply_markup=get_open_app_link(rel.token))
                    await store_bot_message(message.message_id + 1, message.chat.id, bot.id, answer)
                    await create_balance(rel.cashbox_id, message)
            else:

                user_query = users.insert().values(
                    chat_id=str(message.chat.id),
                    owner_id=str(creator.id),
                    photo=user.photo,
                    phone_number=user.phone_number,
                    first_name=user.first_name,
                    last_name=user.last_name,
                    username=user.username,
                    created_at=int(datetime.utcnow().timestamp()),
                    updated_at=int(datetime.utcnow().timestamp())
                )

                user_id = await database.execute(user_query)

                query = users.select().where(users.c.id == user_id)
                user = await database.fetch_one(query)

                rel = await create_cbox(user)
                answer = texts.create_cbox.format(token=rel.token, url=app_url)
                await message.answer(text=answer, reply_markup=get_open_app_link(rel.token))
                await store_bot_message(message.message_id + 1, message.chat.id, bot.id, answer)
                await create_balance(rel.cashbox_id, message)

        else:
            await message.answer(text=texts.not_register,
                                 reply_markup=types.ReplyKeyboardRemove())
            await store_bot_message(message.message_id + 1, message.chat.id, bot.id, texts.not_register)
    else:
        await message.answer(
            text=texts.not_admin_chat.format(creator=creator.username),
            reply_markup=types.ReplyKeyboardRemove())
        await store_bot_message(message.message_id + 1, message.chat.id, bot.id,
                                texts.not_admin_chat.format(creator=creator.username))

    await state.clear()


@router_comm.message(commands="newdelivery")
async def broadcast_message(message: types.Message, state: FSMContext):
    """
    /newdelievery command handler for broadcast message
    """
    await store_user_message(message)
    query = users.select().where(users.c.is_admin == True, users.c.chat_id == str(message.chat.id))
    if await database.fetch_one(query):
        await message.answer(texts.send_broadcast_message)
        await store_bot_message(message.message_id + 1, message.chat.id, bot.id, texts.send_broadcast_message)
        await state.set_state(DeliveryForm.send)


@router_comm.message(content_types=["photo"], state=DeliveryForm.send)
async def confirm_broadcast_message(message: types.Message, state: FSMContext):
    """
    Get and send full message
    :param message: Telegram message instance
    :param state: FSMContext object
    """
    if message.photo and message.md_text:
        # Здесь берем оригинал картинки и переводим в str
        BroadcastMessageStore.picture = str(message.photo[-1])
        BroadcastMessageStore.text = message.md_text.replace("\\", "")
        BroadcastMessageStore.tg_message_id = message.message_id
        BroadcastMessageStore.tg_user_or_chat = str(message.chat.id)
        BroadcastMessageStore.created_at = str(message.date)
        query = users.select().where(users.c.is_blocked == False)
        last_active_users = await database.fetch_all(query)
        await store_user_message(message)
        await message.answer(
            f"Вы уверены что хотите разослать сообщение по {len(last_active_users) - 1} живым пользователям?",
            reply_markup=choose_keyboard)
        await store_bot_message(message.message_id + 1, message.chat.id, bot.id,
                                f"Вы уверены что хотите разослать сообщение "
                                f"по {len(last_active_users) - 1} живым пользователям?")
        await state.set_state(DeliveryForm.submit)


async def prepare_registration(message):
    """Prepares data for the user registration"""
    created = int(datetime.utcnow().timestamp())

    photos = await bot.get_user_profile_photos(user_id=message.from_user.id)
    photo_path = "photos/default.jpg"

    for i in photos.photos[:1]:
        photo_file = await bot.get_file(i[1].file_id)
        photo_path = photo_file.file_path
        await bot.download_file(photo_path, photo_path)

    user_query = users.insert().values(
        chat_id=str(message.chat.id),
        owner_id=str(message.contact.user_id),
        photo=photo_path,
        phone_number=message.contact.phone_number,
        first_name=message.from_user.first_name,
        last_name=message.from_user.last_name,
        username=message.from_user.username,
        created_at=created,
        updated_at=created
    )
    return created, user_query


async def create_balance(cashbox_id, message, tariff=None):
    """Creates balance and sets demo for a new cashbox. Sends message with result."""
    created = int(datetime.utcnow().timestamp())
    if not tariff:
        tariff_query = tariffs.select().where(tariffs.c.actual == True)
        tariff = await database.fetch_one(tariff_query)

    balance_query = accounts_balances.insert().values(
        cashbox=cashbox_id,
        balance=0,
        tariff=tariff.id,
        tariff_type=DEMO,
        created_at=created,
        updated_at=created,
    )
    await database.execute(balance_query)

    await bot.send_message(
        message.chat.id,
        texts.you_got_demo.format(
            n=tariff.demo_days,
            tax=tariff.price,
            for_user=" за пользователя" if tariff.per_user else "",
            link=PAY_LINK,
        ),
    )
    await store_bot_message(message.message_id + 1, message.chat.id, bot.id, texts.you_got_demo.format(
        n=tariff.demo_days,
        tax=tariff.price,
        for_user=" за пользователя" if tariff.per_user else "",
        link=PAY_LINK,
    ))


@router_comm.message(Form.start, content_types=['contact'])
async def reg_user_create(message: types.Message, state: FSMContext):
    await store_user_message(message)
    if message.contact:
        tariff_query = tariffs.select().where(tariffs.c.actual is True)
        tariff = await database.fetch_one(tariff_query)

        created, user_query = await prepare_registration(message)

        user_id = await database.execute(user_query)

        query = users.select().where(users.c.id == user_id)
        user = await database.fetch_one(query)
        rel = await create_cbox(user)

        answer = texts.create_cbox.format(token=rel.token, url=app_url)
        await message.answer(text=answer, reply_markup=get_open_app_link(rel.token))
        await store_bot_message(message.message_id + 1, message.chat.id, bot.id, answer)
        await create_balance(rel.cashbox_id, message, tariff)

        await state.clear()


@router_comm.message(Form.join, content_types=['contact'])
async def reg_user_join(message: types.Message, state: FSMContext):
    await store_user_message(message)
    if message.contact:
        created, user_query = await prepare_registration(message)

        user = await database.fetch_one(user_query)
        data = await state.get_data()
        rel = await join_cbox(user, data['cbox'])
        answer = texts.invite_cbox.format(token=rel.token, url=app_url)
        await message.answer(text=answer, reply_markup=get_open_app_link(rel.token))
        await store_bot_message(message.message_id + 1, message.chat.id, bot.id, answer)
        await state.clear()


@router_comm.message(lambda message: message.text == "Отменить регистрацию", state="*")
async def without_puree(message: types.Message, state: FSMContext):
    await store_user_message(message)
    await state.clear()
    await message.reply("Вы отменили регистрацию!", reply_markup=types.ReplyKeyboardRemove())
    await store_bot_message(message.message_id + 1, message.chat.id, bot.id, "Вы отменили регистрацию!")


@router_comm.message(lambda message: message.text == "Отмена", state="*")
async def cancel_cheque(message: types.Message, state: FSMContext):
    await store_user_message(message)
    await state.clear()
    await message.reply("Вы отменили создание чека!", reply_markup=types.ReplyKeyboardRemove())
    await store_bot_message(message.message_id + 1, message.chat.id, bot.id, "Вы отменили создание чека!")


@router_comm.message(lambda message: message.text == "No", state=DeliveryForm.submit)
async def deny_broadcast_message(message: types.Message, state: FSMContext):
    await store_user_message(message)
    await state.clear()
    await message.reply("Вы отменили отправку рассылки", reply_markup=types.ReplyKeyboardRemove())
    await store_bot_message(message.message_id + 1, message.chat.id, bot.id, "Вы отменили отправку рассылки")


@router_comm.message(lambda message: message.text == "Yes", state=DeliveryForm.submit)
async def sumbit_broadcast_message(message: types.Message, state: FSMContext):
    await store_user_message(message)
    await message.reply("Готово, ваша рассылка запущена, вот тут вы можете видеть статус рассылки.",
                        reply_markup=types.ReplyKeyboardRemove())
    await store_bot_message(message.message_id + 1, message.chat.id, bot.id,
                            "Готово, ваша рассылка запущена, вот тут вы можете видеть статус рассылки.")
    # При реализации хранения картинок нужно подумать над форматом сериализации картинок
    await produce_message({"text": BroadcastMessageStore.text, "picture": BroadcastMessageStore.picture,
                           "tg_message_id": BroadcastMessageStore.tg_message_id,
                           "tg_user_or_chat": BroadcastMessageStore.tg_user_or_chat,
                           "created_at": BroadcastMessageStore.created_at,
                           "message_id": message.message_id})
    await state.clear()


async def finish_broadcast_messaging(chat_id: str, total: int, active_before: int, active_after: int, message_id: int):
    try:
        await bot.send_message(chat_id=chat_id, text=texts.send_broadcast_messaging_logs.format(
            total=total, before=active_before, after=active_after))
        await store_bot_message(message_id, chat_id, bot.id, texts.send_broadcast_messaging_logs.format(
            total=total, before=active_before, after=active_after))
    except Exception as e:
        print("Notification failed")


async def message_to_chat_by_id(chat_id: str, message: str):
    try:
        await bot.send_message(chat_id=chat_id, text=message, parse_mode="markdown")
        print(f"Sent to {chat_id}")
    except Exception as e:
        print(f"Broadcast sending message to {chat_id} exception occured {e}")
        await database.execute(users.update().where(users.c.chat_id == chat_id).values({"is_blocked": True}))
        return False
    return True


@router_add_migrate.message(F.migrate_to_chat_id)
async def group_to_supegroup_migration(message: types.Message):
    query = users.update().where(users.c.chat_id == str(message.chat.id)).values(
        {"chat_id": str(message.migrate_to_chat_id)})
    await database.execute(query)


async def main():
    dp = Dispatcher()

    await database.connect()
    # Register handlers
    dp.include_router(router_comm)
    dp.include_router(router_add_migrate)

    # Set bot commands in UI
    # await set_bot_commands(bot)

    # Run bot
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


if __name__ == "__main__":
    asyncio.run(main())
