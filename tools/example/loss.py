
import numpy as np 
from skimage import io, transform


def calc_loss(pred, target, method=None, select_channel=None):

    if select_channel is not None:
        target = target[:,select_channel]
        pred = pred[:,select_channel]

    if method == "any":
        max_targ,_ = target.max(dim=1,keepdim=True)
        loss = dice_loss(pred, max_targ)
    else:
        loss = dice_loss(pred, target)

    loss = loss.mean()       
    return loss

def dice_loss(pred, target, smooth = .00001):

    #pred = pred ** 2
    #target[target<0.5] = -0.5*target[target>0.5].mean()
    #pred = pred[:,:1]
    #target = target[:,:1]

    
    if pred.ndim == 3: pred = pred[None,:]
    if target.ndim == 3: target = target[None,:]
    #if not (target).any(): target = target+0.00001

    if pred.shape[1] != target.shape[1]:
        print("loss warning - Shapes are different",
                "prediction:", pred.shape, "target:",target.shape)

    intersection = 2*(pred * target).mean(dim=(2,3))
    combination =  (pred**2 + target**2).mean(dim=(2,3))
    dsc = (intersection + smooth) / (combination+smooth) 
    dsc = (1 - dsc)
    return dsc

